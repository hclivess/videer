"""
Quality matching for videer

Answers one question: which CRF/CQ keeps *this* source looking the same, and no higher?

Picking a CRF by habit is a guess about content the number knows nothing about. A clean animated source is
still transparent at CRF 26 while a grainy film print is already falling apart at 20, so a fixed setting either
throws away quality or throws away disk space — and which of the two it did is invisible until the encode is
finished. The search here measures instead: it encodes a few short samples of the real file at candidate CRFs,
scores each against the source with a full-reference metric, and bisects to the *highest* CRF that still meets
the quality target. Highest, because among the settings that preserve quality, the largest CRF is the smallest
file. Cost is bounded: bisecting a 20-step CRF range takes five probes of a few seconds of video each.

Two things decide whether the answer is any good, and both are the user's to choose:

* **Which metric.** No metric is the truth. VMAF is trained on human scores but for 1080p at three screen
  heights, so it reads high on UHD and can be flattered by anything that adds apparent sharpness; NEG removes
  that gain, the 4K model re-anchors the scale, MS-SSIM and SSIM measure structure instead of predicting
  opinion, and PSNR/XPSNR measure signal error alone.
* **How the frames are pooled.** A mean is what everyone quotes and is exactly what hides a bad scene: twenty
  smeared seconds inside ten good minutes barely move it. Pooling on a low percentile or the worst frame asks
  how bad it gets rather than how good it usually is, which is the answer a search should be steering by.

The search engine (QualitySearch) is deliberately free of Qt: it runs both from the dialog's own thread and
from inside the encoding queue, where every file gets its own CRF.
"""

import csv
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from config import (DEFAULT_QUALITY_METRIC, DEFAULT_QUALITY_POOL, DEFAULT_QUALITY_SEARCH_RANGE,
                    QUALITY_METRICS, QUALITY_POOLS, QUALITY_SAMPLE_COUNT, QUALITY_SAMPLE_MARGIN,
                    QUALITY_SAMPLE_SECONDS, QUALITY_SEARCH_RANGE, VIDEO_EXTENSIONS)
from modules.process_manager import format_duration, format_size
from utils import childproc
from utils.ffmpeg_utils import (CRF_ENCODERS, FFmpegCommandBuilder, ffmpeg_has_filter,
                                find_ffmpeg, probe_media_info)


# How long to let a probe linger after it has closed its output before killing it. There is no stall watchdog
# on top of that: a search started from the dialog is one Cancel click from gone, and one running inside the
# queue is killed by the same Stop button that kills an encode.
PROBE_TIMEOUT = 30 * 60

# The per-frame log every metric writes. A bare name, never a path — see build_metric_command.
LOG_NAME = "metric.log"

# PSNR and XPSNR report an identical frame as infinite dB. Nothing useful can be pooled with an infinity in
# it, and no encode is perfect for long, so an identical frame is recorded at the top of the scale instead.
PSNR_CEILING = 100.0


# Probe encodes and per-frame logs live in temp directories with this prefix, removed as soon as the search
# that made them ends.
WORKDIR_PREFIX = "videer-quality-"
VERIFY_PREFIX = "videer-verify-"

# ...unless the app never got the chance: a power cut, a kill from Task Manager or a crash leaves them behind,
# and a probe encode of a 4K source is not a small file to abandon. They are swept at startup, but only once
# they are far older than any search could still be using — another videer running alongside this one may own
# a fresh one.
STALE_WORKDIR_AGE = 24 * 3600


def sweep_stale_workdirs(max_age: float = STALE_WORKDIR_AGE) -> int:
    """Delete probe directories abandoned by an earlier run. Returns how many went."""
    removed = 0
    root = tempfile.gettempdir()
    cutoff = time.time() - max_age
    try:
        names = os.listdir(root)
    except OSError:
        return 0

    for name in names:
        if not name.startswith((WORKDIR_PREFIX, VERIFY_PREFIX)):
            continue
        path = os.path.join(root, name)
        try:
            if not os.path.isdir(path) or os.path.getmtime(path) > cutoff:
                continue
            shutil.rmtree(path, ignore_errors=True)
            removed += not os.path.exists(path)
        except OSError:
            continue
    return removed


# ----------------------------------------------------------------------
# Metrics
# ----------------------------------------------------------------------
def metric_spec(metric: str) -> Dict[str, Any]:
    return QUALITY_METRICS.get(metric) or QUALITY_METRICS[DEFAULT_QUALITY_METRIC]


def metric_available(metric: str) -> bool:
    """Whether this FFmpeg build has the filter that computes the metric"""
    spec = QUALITY_METRICS.get(metric)
    return bool(spec) and ffmpeg_has_filter(spec['filter'])


def available_metrics() -> List[str]:
    return [name for name in QUALITY_METRICS if metric_available(name)]


def choose_metric(preferred: str = DEFAULT_QUALITY_METRIC) -> str:
    """The metric asked for when the build can compute it, otherwise the first it can — SSIM always can"""
    for metric in [preferred, DEFAULT_QUALITY_METRIC] + list(QUALITY_METRICS):
        if metric_available(metric):
            return metric
    return DEFAULT_QUALITY_METRIC


def metric_target(metric: str, value: Any = None) -> float:
    """
    A target that means something on this metric's scale.

    VMAF counts to 100, SSIM to 1.0, XPSNR in decibels. A number carried over from another metric is not a
    demanding target, it is an impossible one: 95 asked of SSIM can never be met, so every probe reads "below
    target", the search walks to the bottom of the CRF range and recommends it — on every file, for a reason
    nothing on screen explains. Anything outside the metric's own range is therefore not a target at all, and
    the metric's default is used instead.
    """
    spec = metric_spec(metric)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(spec['default_target'])
    low, high = spec['range']
    return number if low <= number <= high else float(spec['default_target'])


def substitution_note(requested: str, used: str, target: float) -> str:
    """Why the search is not measuring what it was asked to measure"""
    asked, actual = metric_spec(requested), metric_spec(used)
    return (f"This FFmpeg has no {asked['filter']} filter, so {asked['label']} cannot be measured — "
            f"searching on {actual['label']} \u2265 {target:g}{actual['unit']} instead. VMAF and MS-SSIM need "
            f"an FFmpeg built with libvmaf; SSIM, PSNR and XPSNR are in every build.")


def format_score(metric: str, value: Optional[float]) -> str:
    """'VMAF 95.2' / 'PSNR 42.10 dB' — the number with enough context to be read on its own"""
    if value is None:
        return "--"
    spec = metric_spec(metric)
    return f"{spec['label']} {value:.{spec['decimals']}f}{spec['unit']}"


def format_value(metric: str, value: Optional[float]) -> str:
    """Just the number, for table cells that already have the metric in their header"""
    if value is None:
        return "--"
    spec = metric_spec(metric)
    return f"{value:.{spec['decimals']}f}"


def pool_label(pool: str) -> str:
    for label, key in QUALITY_POOLS:
        if key == pool:
            return label.split(' — ')[0]
    return pool


def pool_scores(values: List[float], method: str = DEFAULT_QUALITY_POOL) -> Optional[float]:
    """
    Reduce per-frame scores to the one number the search steers by.

    The percentile pools are the reason per-frame logs are collected at all: on a mean, a search happily
    trades away the handful of frames that actually fall apart, because the average never notices them.
    """
    values = [v for v in values if v is not None and v == v]          # NaN guard
    if not values:
        return None

    if method == 'min':
        return min(values)
    if method == 'harmonic':
        # Undefined at or below zero, and dB metrics can legitimately be small; fall back to the mean rather
        # than return something invented.
        if any(v <= 0 for v in values):
            return sum(values) / len(values)
        return len(values) / sum(1.0 / v for v in values)
    if method in ('p1', 'p5'):
        percentile = 1.0 if method == 'p1' else 5.0
        ordered = sorted(values)
        # Nearest-rank: the value at or below which that share of frames falls. With few frames it lands on
        # the worst one, which is the honest answer for a short sample.
        rank = max(1, int(math.ceil(percentile / 100.0 * len(ordered))))
        return ordered[rank - 1]
    return sum(values) / len(values)


def parse_metric_log(log_path: str, metric: str) -> List[float]:
    """Per-frame scores from the log the filter wrote; empty when there is nothing readable"""
    spec = metric_spec(metric)
    if not log_path or not os.path.isfile(log_path):
        return []

    values: List[float] = []
    try:
        if spec['family'] == 'libvmaf':
            with open(log_path, 'r', encoding='utf-8', errors='replace', newline='') as handle:
                for row in csv.DictReader(handle):
                    raw = row.get(spec['column'])
                    if raw not in (None, ''):
                        values.append(float(raw))
        else:
            pattern = re.compile(spec['frame_re'])
            with open(log_path, 'r', encoding='utf-8', errors='replace') as handle:
                for line in handle:
                    match = pattern.search(line)
                    if match:
                        raw = match.group(1)
                        values.append(PSNR_CEILING if raw == 'inf' else float(raw))
    except (OSError, ValueError, csv.Error):
        return values
    return values


def last_complaint(lines: List[str]) -> Optional[str]:
    """FFmpeg's last complaint, for when a metric produced nothing and the reason is in its output"""
    for line in reversed(lines):
        lowered = line.lower()
        if any(word in lowered for word in ('error', 'invalid', 'not found', 'unable to', 'no such')):
            return line[-200:]
    return None


def parse_metric_summary(lines: List[str], metric: str) -> Optional[float]:
    """The single figure the filter prints when it finishes — the fallback if the log is unusable"""
    spec = metric_spec(metric)
    if not spec.get('summary_re'):
        return None
    pattern = re.compile(spec['summary_re'], re.IGNORECASE)
    for line in reversed(lines):
        match = pattern.search(line)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
    return None


# ----------------------------------------------------------------------
# Search settings and sampling
# ----------------------------------------------------------------------
def search_range_for(video_codec: str) -> Tuple[int, int]:
    return QUALITY_SEARCH_RANGE.get(video_codec, DEFAULT_QUALITY_SEARCH_RANGE)


def resolved_search_range(settings: Dict[str, Any]) -> Tuple[int, int]:
    """The CRF window to bisect: what the user set, or the encoder's own default window"""
    default_low, default_high = search_range_for(settings.get('video_codec'))
    low = int(settings.get('quality_crf_low') or 0) or default_low
    high = int(settings.get('quality_crf_high') or 0) or default_high
    return (low, high) if low <= high else (high, low)


def plan_samples(duration: Optional[float], count: int, seconds: float,
                 margin: float = QUALITY_SAMPLE_MARGIN) -> List[Tuple[float, float]]:
    """
    Where to cut the sample clips. They are spread evenly over the middle of the file: the first and last few
    percent are titles, logos and credits, which compress nothing like the content between them and would
    drag the answer towards a CRF that is wrong for the whole file.
    """
    seconds = max(1.0, float(seconds))
    count = max(1, int(count))

    if not duration or duration <= 0:
        return [(0.0, seconds)]

    if duration <= seconds * 1.2:
        return [(0.0, max(1.0, duration - 0.05))]        # short clip: score all of it

    usable_start = duration * margin
    usable_end = duration * (1.0 - margin)
    usable = usable_end - usable_start
    if usable < seconds:
        usable_start, usable = 0.0, duration

    count = max(1, min(count, int(usable // seconds)))
    if count == 1:
        return [(max(0.0, (duration - seconds) / 2.0), seconds)]

    step = (usable - seconds) / (count - 1)
    return [(usable_start + i * step, seconds) for i in range(count)]


# ----------------------------------------------------------------------
# The search itself
# ----------------------------------------------------------------------
class QualitySearch:
    """
    Bisects the CRF range for one file. No Qt: the dialog drives it from a worker thread and the encoding
    queue drives it inline, and neither wants a second QThread inside it.

    The host supplies `should_stop` and gets `on_process` for every child that is spawned, so whichever Stop
    button the user reaches for can reach the probe that is running.
    """

    def __init__(self, filepath: str, settings: Dict[str, Any],
                 on_progress: Optional[Callable[[str], None]] = None,
                 on_probe: Optional[Callable[[Dict[str, Any]], None]] = None,
                 on_step: Optional[Callable[[int, int], None]] = None,
                 should_stop: Optional[Callable[[], bool]] = None,
                 on_process: Optional[Callable[[Optional[subprocess.Popen]], None]] = None):
        self.filepath = filepath
        self.settings = dict(settings)
        self._on_progress = on_progress
        self._on_probe = on_probe
        self._on_step = on_step
        self._should_stop = should_stop or (lambda: False)
        self._on_process = on_process

        requested = self.settings.get('quality_metric', DEFAULT_QUALITY_METRIC)
        self.metric = choose_metric(requested)
        # A target set for a metric this build cannot compute belongs to that metric, not to the stand-in
        self.substituted_for = requested if (requested != self.metric and requested in QUALITY_METRICS) else None
        self.target = metric_target(self.metric,
                                    None if self.substituted_for else self.settings.get('quality_target'))
        self.pool = self.settings.get('quality_pool', DEFAULT_QUALITY_POOL)
        self.sample_count = int(self.settings.get('quality_samples') or QUALITY_SAMPLE_COUNT)
        self.sample_seconds = float(self.settings.get('quality_sample_seconds') or QUALITY_SAMPLE_SECONDS)
        self.crf_low, self.crf_high = resolved_search_range(self.settings)

        self.error: Optional[str] = None
        self._proc: Optional[subprocess.Popen] = None
        self._workdir: Optional[str] = None
        self._steps_done = 0
        self._steps_expected = 1

    # ------------------------------------------------------------------
    def cancelled(self) -> bool:
        return bool(self._should_stop())

    def kill_current(self):
        proc = self._proc
        if proc is not None:
            childproc.kill(proc)

    def _progress(self, message: str):
        if self._on_progress:
            self._on_progress(message)

    def _advance(self):
        self._steps_done += 1
        self._steps_expected = max(self._steps_expected, self._steps_done)
        if self._on_step:
            self._on_step(self._steps_done, self._steps_expected)

    # ------------------------------------------------------------------
    def run(self) -> Optional[Dict[str, Any]]:
        """The whole search. Returns the summary, or None if it was cancelled or could not run."""
        try:
            return self._search()
        except Exception as exc:                          # noqa: BLE001 - a caller must never lose the thread
            if not self.cancelled():
                self.error = f"{type(exc).__name__}: {exc}"
            return None
        finally:
            if self._workdir:
                shutil.rmtree(self._workdir, ignore_errors=True)
                self._workdir = None

    def _search(self) -> Optional[Dict[str, Any]]:
        if not find_ffmpeg():
            self.error = "FFmpeg was not found."
            return None
        if self.settings.get('video_codec') not in CRF_ENCODERS:
            self.error = f"'{self.settings.get('video_codec')}' has no CRF to search."
            return None
        if not metric_available(self.metric):
            self.error = (f"This FFmpeg build has no {metric_spec(self.metric)['filter']} filter, "
                          f"so quality cannot be measured.")
            return None

        info = probe_media_info(self.filepath)
        duration = info.get('duration')
        samples = plan_samples(duration, self.sample_count, self.sample_seconds)

        # Bisection halves the range each time, so the number of probes is known before the first one runs
        span = max(1, self.crf_high - self.crf_low + 1)
        probes_expected = max(1, int(math.ceil(math.log2(span + 1))))
        self._steps_expected = probes_expected * len(samples) * 2
        self._steps_done = 0

        self._progress(
            f"Source: {format_duration(duration)}, {format_size(info.get('size'))}"
            + (f", {info['width']}x{info['height']}" if info.get('width') else "")
            + (f", {info['codec']}" if info.get('codec') else ""))
        self._progress(
            f"{len(samples)} sample(s) of {format_duration(samples[0][1])}, "
            f"{metric_spec(self.metric)['label']} ≥ {self.target:g} "
            f"({pool_label(self.pool).lower()}), CRF {self.crf_low}–{self.crf_high} "
            f"— about {probes_expected} probes.")
        if self.substituted_for:
            self._progress(substitution_note(self.substituted_for, self.metric, self.target))

        self._workdir = tempfile.mkdtemp(prefix=WORKDIR_PREFIX)

        results: Dict[int, Dict[str, Any]] = {}
        low, high = self.crf_low, self.crf_high
        best: Optional[Dict[str, Any]] = None

        while low <= high and not self.cancelled():
            crf = (low + high) // 2
            probe = self._evaluate(crf, samples, info)
            if probe is None:
                return None                               # cancelled, or already recorded in self.error
            results[crf] = probe
            if self._on_probe:
                self._on_probe(probe)

            if probe['meets_target']:
                best = probe
                low = crf + 1                             # quality to spare: try to save more space
            else:
                high = crf - 1                            # went too far: back off

        if self.cancelled():
            return None
        return self._summarize(best, results, info, samples)

    # ------------------------------------------------------------------
    def _evaluate(self, crf: int, samples: List[Tuple[float, float]],
                  info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Encode every sample at this CRF and score it — returns None if cancelled or the probe failed"""
        builder = FFmpegCommandBuilder({**self.settings, 'crf': crf})
        threads = int(self.settings.get('threads') or 0)

        frame_scores: List[float] = []
        sample_scores: List[float] = []
        encoded_bytes = 0
        encoded_seconds = 0.0

        for number, (start, length) in enumerate(samples, start=1):
            if self.cancelled():
                return None
            label = f"CRF {crf} · sample {number}/{len(samples)} at {format_duration(start)}"

            sample_path = os.path.join(self._workdir, f"probe_crf{crf}_{number}.mkv")
            self._progress(f"{label}: encoding…")
            command = builder.build_sample_encode_command(self.filepath, sample_path, start, length)
            if self._run(command) != 0:
                if self.cancelled():
                    return None
                self.error = f"The probe encode at CRF {crf} failed — see the log for FFmpeg's reason."
                return None
            self._advance()

            try:
                encoded_bytes += os.path.getsize(sample_path)
            except OSError:
                pass
            encoded_seconds += length

            self._progress(f"{label}: scoring…")
            frames, summary, complaint = self._measure(builder, sample_path, start, length, threads)
            if not frames and summary is None:
                if self.cancelled():
                    return None
                # An older libvmaf has the filter but not the options the NEG and 4K models need, so
                # "the filter is missing" would be the wrong thing to tell the user. FFmpeg's own last
                # error line says which it is.
                self.error = (f"Could not read a {metric_spec(self.metric)['label']} score at CRF {crf}"
                              + (f": {complaint}" if complaint else
                                 f". Check that this FFmpeg build has the "
                                 f"{metric_spec(self.metric)['filter']} filter."))
                return None
            frame_scores.extend(frames)
            sample_scores.append(pool_scores(frames, self.pool) if frames else summary)
            self._advance()

            try:
                os.remove(sample_path)
            except OSError:
                pass

        # Pooled across every frame of every sample at once, not as an average of per-sample figures: on a
        # percentile pool those are different questions, and "the worst frames anywhere in the file" is the
        # one worth asking.
        score = pool_scores(frame_scores, self.pool)
        if score is None:
            score = pool_scores([s for s in sample_scores if s is not None], 'mean')
        if score is None:
            self.error = f"No usable {metric_spec(self.metric)['label']} score at CRF {crf}."
            return None

        bitrate = (encoded_bytes * 8 / encoded_seconds) if encoded_seconds else None
        duration = info.get('duration')
        estimated = (bitrate * duration / 8) if (bitrate and duration) else None

        probe = {
            'crf': crf,
            'score': score,
            'sample_scores': sample_scores,
            'frames': len(frame_scores),
            'bitrate': bitrate,
            'estimated_size': estimated,
            'source_size': info.get('size'),
            'meets_target': score >= self.target,
        }
        self._progress(
            f"CRF {crf}: {format_score(self.metric, score)} "
            f"({'meets' if probe['meets_target'] else 'below'} target)"
            + (f", ≈{format_size(estimated)}" if estimated else ""))
        return probe

    def _measure(self, builder: FFmpegCommandBuilder, sample_path: str, start: float,
                 length: float, threads: int) -> Tuple[List[float], Optional[float], Optional[str]]:
        """Score one encoded sample against the same segment of the source"""
        log_dir = tempfile.mkdtemp(prefix="metric-", dir=self._workdir)
        lines: List[str] = []
        command = builder.build_metric_command(sample_path, self.filepath, self.metric,
                                               start=start, duration=length, threads=threads,
                                               log_name=LOG_NAME)
        try:
            self._run(command, lines.append, cwd=log_dir)
            frames = parse_metric_log(os.path.join(log_dir, LOG_NAME), self.metric)
            return frames, parse_metric_summary(lines, self.metric), last_complaint(lines)
        finally:
            shutil.rmtree(log_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    def _run(self, command: List[str], on_line=None, cwd: Optional[str] = None) -> int:
        """Run one FFmpeg step, feeding its output to on_line. Returns the exit code (1 when cancelled)."""
        try:
            proc = childproc.popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, universal_newlines=True,
                                   encoding='utf-8', errors='replace', bufsize=1, cwd=cwd)
        except OSError as exc:
            self.error = f"Could not start FFmpeg: {exc}"
            return 1

        self._proc = proc
        if self._on_process:
            self._on_process(proc)
        try:
            for raw_line in proc.stdout:
                if self.cancelled():
                    childproc.kill(proc)
                    return 1
                line = raw_line.strip()
                if line and on_line:
                    on_line(line)
            try:
                return proc.wait(timeout=PROBE_TIMEOUT)
            except subprocess.TimeoutExpired:
                childproc.kill(proc)
                return 1
        finally:
            childproc.release(proc)
            self._proc = None
            if self._on_process:
                self._on_process(None)

    # ------------------------------------------------------------------
    def _summarize(self, best: Optional[Dict[str, Any]], results: Dict[int, Dict[str, Any]],
                   info: Dict[str, Any], samples: List[Tuple[float, float]]) -> Dict[str, Any]:
        """Turn the probes into a recommendation plus the caveats that go with it"""
        source_size = info.get('size')
        notes: List[str] = []
        if self.substituted_for:
            notes.append(substitution_note(self.substituted_for, self.metric, self.target))

        if best is None:
            # Nothing in the range held the target. The closest attempt is the honest recommendation, and the
            # reason is almost always grain or noise: detail that costs a great many bits to reproduce.
            closest = max(results.values(), key=lambda p: p['score']) if results else None
            best_line = (f" The best anything in that range managed was "
                         f"{format_score(self.metric, closest['score'])} at CRF {closest['crf']}, so a target "
                         f"above that is out of reach for this source." if closest else "")
            notes.append(
                f"No CRF in {self.crf_low}–{self.crf_high} reached "
                f"{format_score(self.metric, self.target)}.{best_line} A grainy, noisy or already heavily "
                f"compressed source is the usual reason — detail that expensive to keep is also a sign "
                f"that re-encoding it will not save much.")
            recommended = closest
        else:
            recommended = best
            if best['crf'] >= self.crf_high:
                notes.append(
                    f"CRF {self.crf_high} was the top of the search range and still met the target — raise "
                    f"the range to find out how much further this file can go.")
            if best['crf'] <= self.crf_low:
                notes.append(
                    f"CRF {self.crf_low} was the bottom of the range; a lower one may be needed for this "
                    f"source.")

        estimated = recommended.get('estimated_size') if recommended else None
        savings = None
        if estimated and source_size:
            savings = 1.0 - (estimated / source_size)
            if savings <= 0.05:
                notes.append(
                    "The estimate is no smaller than the source: at this quality there is nothing to gain by "
                    "re-encoding. Copying the stream keeps it exactly as it is, for free.")

        if self.metric == 'vmaf' and (info.get('height') or 0) >= 1800:
            notes.append("This is a UHD source and plain VMAF is anchored to 1080p viewing, where it reads "
                         "several points high. The VMAF 4K model is the one built for it.")
        if self.pool == 'mean':
            notes.append("Pooled on the mean, which averages away the worst scenes. If the encode looks worse "
                         "than the score promised, search again on the 5th percentile.")
        if self.settings.get('use_avisynth'):
            notes.append("AviSynth+ processing is enabled but is not applied to the probes, so the real "
                         "encode will differ from this estimate.")
        if self.settings.get('reduce_fps') and self.settings.get('deinterlace'):
            notes.append("Halving the frame rate while deinterlacing changes the frame count, which the "
                         "metric cannot compare frame-for-frame; treat the score as approximate.")

        return {
            'file': self.filepath,
            'metric': self.metric,
            'pool': self.pool,
            'target': self.target,
            'recommended': recommended,
            'reached_target': best is not None,
            'probes': [results[crf] for crf in sorted(results)],
            'source_size': source_size,
            'duration': info.get('duration'),
            'samples': samples,
            'estimated_size': estimated,
            'savings': savings,
            'notes': notes,
        }


class QualityAnalyzer(QThread):
    """Runs a QualitySearch on a worker thread and reports it as signals — the dialog's half of the feature"""

    progress = Signal(str)              # human-readable step
    step_advanced = Signal(int, int)    # steps done, steps expected
    probe_finished = Signal(dict)       # one CRF evaluated
    analysis_finished = Signal(dict)    # the recommendation
    failed = Signal(str)

    def __init__(self, filepath: str, settings: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.should_stop = False
        self.search = QualitySearch(
            filepath, settings,
            on_progress=self.progress.emit,
            on_probe=self.probe_finished.emit,
            on_step=self.step_advanced.emit,
            should_stop=lambda: self.should_stop)

    def stop(self):
        """Cancel: the probe in flight is killed, the search unwinds on its own"""
        self.should_stop = True
        self.search.kill_current()

    def run(self):
        result = self.search.run()
        if result is not None:
            self.analysis_finished.emit(result)
        elif self.search.error and not self.should_stop:
            self.failed.emit(self.search.error)


class QualityMatchDialog(QDialog):
    """
    Interactive front end for the search: pick a file, pick what "same quality" means, get the CRF.

    The encoder, speed preset and filter settings come from the main window as they stand — the point is to
    answer for the encode the user is actually about to run. The quality settings start from the Quality tab
    and are written back to it when a result is applied, so whatever criteria were just validated by eye are
    the ones the batch matcher will use on the rest of the queue.
    """

    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self.main_window = main_window
        self.setWindowTitle("Match Source Quality")
        self.setMinimumWidth(720)

        self.analyzer: Optional[QualityAnalyzer] = None
        self.search_result: Optional[Dict[str, Any]] = None
        self._syncing_target = False
        self._targets_seen: Dict[str, float] = {}

        settings = main_window.ui_manager.get_current_settings()
        self.metric_key = choose_metric(settings.get('quality_metric', DEFAULT_QUALITY_METRIC))

        self._build_ui()
        self._load_from_settings(settings)
        self._populate_files()
        self._sync_to_codec()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Encodes a few short samples of the source at different CRF values, compares each against the "
            "original, and reports the highest CRF — the smallest file — that still meets the quality you "
            "ask for.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #555;")
        layout.addWidget(intro)

        # ---- source -------------------------------------------------
        source_group = QGroupBox("Source")
        source_row = QHBoxLayout(source_group)
        self.file_combo = QComboBox()
        self.file_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._on_browse)
        source_row.addWidget(self.file_combo, 1)
        source_row.addWidget(browse)
        layout.addWidget(source_group)

        # ---- what to aim for ----------------------------------------
        target_group = QGroupBox("Target")
        form = QFormLayout(target_group)

        self.metric_combo = QComboBox()
        fill_metric_combo(self.metric_combo)
        self.metric_combo.currentIndexChanged.connect(self._on_metric_changed)
        self.metric_note = QLabel()
        self.metric_note.setWordWrap(True)
        self.metric_note.setStyleSheet("color: #666; font-size: 11px;")
        form.addRow("Metric:", self.metric_combo)
        form.addRow("", self.metric_note)

        self.target_combo = QComboBox()
        self.target_combo.currentIndexChanged.connect(self._on_target_preset)
        self.target_spin = QDoubleSpinBox()
        self.target_spin.valueChanged.connect(self._on_target_value)

        target_row = QWidget()
        target_layout = QHBoxLayout(target_row)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.addWidget(self.target_combo, 1)
        target_layout.addWidget(self.target_spin)
        form.addRow("Quality to keep:", target_row)

        self.pool_combo = QComboBox()
        for label, key in QUALITY_POOLS:
            self.pool_combo.addItem(label, key)
        self.pool_combo.setToolTip(
            "How the per-frame scores become one number.\n"
            "The mean averages away the worst scenes; a low percentile steers by them instead.")
        form.addRow("Pool frames by:", self.pool_combo)

        self.samples_spin = QSpinBox()
        self.samples_spin.setRange(1, 10)
        self.samples_spin.setToolTip(
            "How many places in the file to measure. More samples describe a varied source better;\n"
            "each one multiplies the time the search takes.")
        self.seconds_spin = QSpinBox()
        self.seconds_spin.setRange(2, 120)
        self.seconds_spin.setSuffix(" s")
        self.seconds_spin.setToolTip("Length of each sample clip.")

        sample_row = QWidget()
        sample_layout = QHBoxLayout(sample_row)
        sample_layout.setContentsMargins(0, 0, 0, 0)
        sample_layout.addWidget(self.samples_spin)
        sample_layout.addWidget(QLabel("samples of"))
        sample_layout.addWidget(self.seconds_spin)
        sample_layout.addStretch()
        form.addRow("Sampling:", sample_row)

        self.crf_low_spin = QSpinBox()
        self.crf_low_spin.setRange(0, 63)
        self.crf_high_spin = QSpinBox()
        self.crf_high_spin.setRange(0, 63)
        range_row = QWidget()
        range_layout = QHBoxLayout(range_row)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.addWidget(self.crf_low_spin)
        range_layout.addWidget(QLabel("to"))
        range_layout.addWidget(self.crf_high_spin)
        range_layout.addStretch()
        form.addRow("Search CRF range:", range_row)

        self.encoder_label = QLabel()
        self.encoder_label.setWordWrap(True)
        self.encoder_label.setStyleSheet("color: #555;")
        form.addRow("Using:", self.encoder_label)

        layout.addWidget(target_group)

        # ---- run ----------------------------------------------------
        run_row = QHBoxLayout()
        self.analyze_button = QPushButton("Analyze")
        self.analyze_button.setStyleSheet(
            "QPushButton { font-weight: bold; padding: 6px 14px; background-color: #0d6efd; color: white;"
            " border-radius: 4px; } QPushButton:disabled { background-color: #cccccc; }")
        self.analyze_button.clicked.connect(self._on_analyze)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._on_cancel)
        run_row.addWidget(self.analyze_button)
        run_row.addWidget(self.cancel_button)
        run_row.addStretch()
        layout.addLayout(run_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Ready.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # ---- results -------------------------------------------------
        self.table = QTableWidget(0, 5)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(140)
        self.table.setToolTip(
            "Estimated size of the video stream over the whole file, scaled up from the samples.\n"
            "Audio, subtitles and container overhead come on top.")
        self._label_table()
        layout.addWidget(self.table)

        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setVisible(False)
        layout.addWidget(self.result_label)

        # ---- buttons -------------------------------------------------
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.apply_button = self.buttons.addButton("Use this CRF",
                                                   QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._on_apply)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _label_table(self):
        self.table.setHorizontalHeaderLabels(
            ["CRF", metric_spec(self.metric_key)['label'], "Est. video", "vs source", "Verdict"])

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    def _populate_files(self):
        """Offer the queue first — that is what the user is about to encode"""
        self.file_combo.clear()
        for file in self.main_window.file_manager.get_queue():
            self.file_combo.addItem(file.filename, file.filepath)
        if self.file_combo.count() == 0:
            self.file_combo.addItem("No files in the queue — use Browse…", None)

    def _sync_to_codec(self):
        """Reflect the encode settings the search will use, and refuse the ones it cannot search"""
        settings = self.main_window.ui_manager.get_current_settings()
        codec = settings.get('video_codec')
        self.encoder_label.setText(
            f"{codec} · speed preset “{settings.get('preset')}”")

        low, high = resolved_search_range(settings)
        self.crf_low_spin.setValue(low)
        self.crf_high_spin.setValue(high)

        searchable = codec in CRF_ENCODERS
        self.analyze_button.setEnabled(searchable)
        if not searchable:
            self.status_label.setText(
                f"‘{codec}’ has no CRF to search — pick x264, x265, AV1, VP9 or NVENC on the Video tab.")

    def _load_from_settings(self, settings: Dict[str, Any]):
        """Start from the Quality tab, so the dialog and the batch matcher agree until told otherwise"""
        index = self.metric_combo.findData(self.metric_key)
        self.metric_combo.setCurrentIndex(index if index >= 0 else 0)
        self._configure_target(metric_target(self.metric_key, settings.get('quality_target')))

        pool_index = self.pool_combo.findData(settings.get('quality_pool', DEFAULT_QUALITY_POOL))
        self.pool_combo.setCurrentIndex(pool_index if pool_index >= 0 else 0)
        self.samples_spin.setValue(int(settings.get('quality_samples') or QUALITY_SAMPLE_COUNT))
        self.seconds_spin.setValue(int(settings.get('quality_sample_seconds') or QUALITY_SAMPLE_SECONDS))

    def _configure_target(self, value: Optional[float] = None):
        """Re-scale the target controls to the current metric — 0-100, 0-1 and decibels share no numbers"""
        spec = metric_spec(self.metric_key)
        self._syncing_target = True
        self.target_combo.clear()
        for label, preset in spec['targets']:
            self.target_combo.addItem(label, preset)
        self.target_combo.addItem("Custom", None)

        self.target_spin.setRange(*spec['range'])
        self.target_spin.setDecimals(spec['decimals'])
        self.target_spin.setSingleStep(spec['step'])
        self.target_spin.setSuffix(spec['unit'])
        self.metric_note.setText(spec['note'])
        self._syncing_target = False

        self.target_spin.setValue(value if value is not None else spec['default_target'])
        self._match_target_preset()

    # ---- metric / target controls ---------------------------------------
    def _on_metric_changed(self, _index):
        metric = self.metric_combo.currentData()
        if not metric or metric == self.metric_key:
            return
        self._targets_seen[self.metric_key] = self.target_spin.value()
        self.metric_key = metric
        # A target is only meaningful on its own metric's scale, so switching brings back what was last used
        # for the new one, or its recommended default.
        self._configure_target(self._targets_seen.get(metric))
        self._label_table()

    def _on_target_preset(self, _index):
        if self._syncing_target:
            return
        value = self.target_combo.currentData()
        if value is not None:
            self._syncing_target = True
            self.target_spin.setValue(float(value))
            self._syncing_target = False

    def _on_target_value(self, _value):
        if self._syncing_target:
            return
        self._match_target_preset()

    def _match_target_preset(self):
        """Show the preset whose value is in the box, or Custom"""
        self._syncing_target = True
        index = self.target_combo.count() - 1                 # Custom
        for i in range(self.target_combo.count()):
            data = self.target_combo.itemData(i)
            if data is not None and abs(float(data) - self.target_spin.value()) < 1e-9:
                index = i
                break
        self.target_combo.setCurrentIndex(index)
        self._syncing_target = False

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _search_settings(self) -> Dict[str, Any]:
        """The encode settings from the tabs, with this dialog's quality choices laid over them"""
        settings = self.main_window.ui_manager.get_current_settings()
        settings.update({
            'quality_metric': self.metric_key,
            'quality_target': self.target_spin.value(),
            'quality_pool': self.pool_combo.currentData(),
            'quality_samples': self.samples_spin.value(),
            'quality_sample_seconds': self.seconds_spin.value(),
            'quality_crf_low': self.crf_low_spin.value(),
            'quality_crf_high': self.crf_high_spin.value(),
        })
        return settings

    def _on_browse(self):
        extensions = " ".join(f"*{ext}" for ext in VIDEO_EXTENSIONS)
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Choose a video to analyze", "", f"Video Files ({extensions});;All Files (*.*)")
        if filepath:
            self.file_combo.insertItem(0, os.path.basename(filepath), filepath)
            self.file_combo.setCurrentIndex(0)

    def _on_analyze(self):
        if self.main_window.process_manager.is_processing():
            QMessageBox.information(
                self, "Busy",
                "The queue is encoding. The search needs the machine to itself to give a usable answer — "
                "run it before starting the queue, or after it finishes.")
            return

        filepath = self.file_combo.currentData()
        if not filepath or not os.path.isfile(filepath):
            QMessageBox.warning(self, "No File", "Choose a video file to analyze.")
            return

        if self.crf_low_spin.value() > self.crf_high_spin.value():
            QMessageBox.warning(self, "Check the range",
                                "The low end of the CRF range must not be above the high end.")
            return

        if not metric_available(self.metric_key):
            QMessageBox.warning(
                self, "Metric unavailable",
                f"This FFmpeg build has no {metric_spec(self.metric_key)['filter']} filter, so "
                f"{metric_spec(self.metric_key)['label']} cannot be measured. Install an FFmpeg built with "
                f"libvmaf, or choose SSIM or PSNR, which every build has.")
            return

        self.search_result = None
        self.table.setRowCount(0)
        self.result_label.setVisible(False)
        self.apply_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self._set_running(True)

        self.analyzer = QualityAnalyzer(filepath, self._search_settings(), parent=self)
        self.analyzer.progress.connect(self.status_label.setText)
        self.analyzer.step_advanced.connect(self._on_step)
        self.analyzer.probe_finished.connect(self._on_probe)
        self.analyzer.analysis_finished.connect(self._on_finished)
        self.analyzer.failed.connect(self._on_failed)
        self.analyzer.finished.connect(lambda: self._set_running(False))
        self.analyzer.start()

    def _on_cancel(self):
        if self.analyzer and self.analyzer.isRunning():
            self.status_label.setText("Cancelling…")
            self.analyzer.stop()

    def _set_running(self, running: bool):
        codec = self.main_window.ui_manager.get_current_settings().get('video_codec')
        self.analyze_button.setEnabled(not running and codec in CRF_ENCODERS)
        self.cancel_button.setEnabled(running)
        for widget in (self.file_combo, self.metric_combo, self.target_combo, self.target_spin,
                       self.pool_combo, self.samples_spin, self.seconds_spin,
                       self.crf_low_spin, self.crf_high_spin):
            widget.setEnabled(not running)
        if not running and self.analyzer and self.analyzer.should_stop:
            self.status_label.setText("Cancelled.")

    def _on_step(self, done: int, expected: int):
        self.progress_bar.setValue(int(min(100, done * 100 / max(1, expected))))

    def _on_probe(self, probe: Dict[str, Any]):
        """Add one measured CRF to the table, kept in CRF order"""
        estimated = probe.get('estimated_size')
        source_size = probe.get('source_size')
        ratio = (f"{estimated / source_size * 100:.0f}%" if estimated and source_size else "--")

        row = self.table.rowCount()
        self.table.insertRow(row)
        for column, text in enumerate([
                str(probe['crf']),
                format_value(self.metric_key, probe['score']),
                format_size(estimated) if estimated else "--",
                ratio,
                "meets target" if probe['meets_target'] else "below target"]):
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if column == 0:
                # sort as a number: as text, CRF 10 would come before CRF 9
                item.setData(Qt.ItemDataRole.DisplayRole, int(probe['crf']))
            if column == 4:
                item.setForeground(Qt.GlobalColor.darkGreen if probe['meets_target']
                                   else Qt.GlobalColor.darkRed)
            self.table.setItem(row, column, item)
        self.table.sortItems(0)

    def _on_finished(self, result: Dict[str, Any]):
        self.search_result = result
        recommended = result.get('recommended')

        if not recommended:
            self.result_label.setText("No usable measurement — see the log above.")
            self.result_label.setStyleSheet("color: #a94442; font-weight: bold;")
            self.result_label.setVisible(True)
            return

        savings = result.get('savings')
        headline = (f"Recommended CRF {recommended['crf']} — "
                    f"{format_score(self.metric_key, recommended['score'])} "
                    f"({pool_label(result['pool']).lower()})")
        if result.get('estimated_size'):
            headline += f", about {format_size(result['estimated_size'])} of video"
            if savings is not None:
                headline += (f" ({savings * 100:.0f}% smaller than the source)" if savings > 0
                             else f" ({-savings * 100:.0f}% larger than the source)")
        if not result.get('reached_target'):
            headline += " — target not reached"

        detail = "\n".join(f"• {note}" for note in result.get('notes', []))
        self.result_label.setText(headline + (f"\n{detail}" if detail else ""))
        self.result_label.setStyleSheet(
            "font-weight: bold; color: %s;" % ("#1e7e34" if result.get('reached_target') else "#8a6d3b"))
        self.result_label.setVisible(True)
        self.apply_button.setEnabled(True)
        self.status_label.setText("Done.")
        self.progress_bar.setValue(100)

    def _on_failed(self, message: str):
        self.status_label.setText(message)
        self.result_label.setText(message)
        self.result_label.setStyleSheet("color: #a94442; font-weight: bold;")
        self.result_label.setVisible(True)

    def _on_apply(self):
        """
        Put the answer where the encode will use it — and the criteria that produced it where the batch
        matcher will use them, so the rest of the queue is judged the way this file just was.
        """
        if not self.search_result or not self.search_result.get('recommended'):
            return
        crf = int(self.search_result['recommended']['crf'])
        ui = self.main_window.ui_manager
        ui.controls['crf'].setValue(crf)
        ui.apply_quality_settings({
            'quality_metric': self.metric_key,
            'quality_target': self.target_spin.value(),
            'quality_pool': self.pool_combo.currentData(),
            'quality_samples': self.samples_spin.value(),
            'quality_sample_seconds': self.seconds_spin.value(),
            'quality_crf_low': self.crf_low_spin.value(),
            'quality_crf_high': self.crf_high_spin.value(),
        })
        ui.update_status(
            f"CRF set to {crf} from the quality match of {os.path.basename(self.search_result['file'])} "
            f"({format_score(self.metric_key, self.search_result['recommended']['score'])})")
        self.accept()

    # ------------------------------------------------------------------
    def done(self, code):
        """
        Every way out of the dialog lands here — Close, Escape, the window's X, Use this CRF. A probe encode
        must never outlive it: the thread is a child of this dialog, and a running QThread whose parent is
        being destroyed takes the application down with it.
        """
        self._shutdown()
        super().done(code)

    def closeEvent(self, event):
        self._shutdown()
        super().closeEvent(event)

    def _shutdown(self):
        if self.analyzer and self.analyzer.isRunning():
            self.analyzer.stop()
            self.analyzer.wait(5000)


def fill_metric_combo(combo: QComboBox):
    """
    Every metric, with the ones this FFmpeg build cannot compute left visible but unselectable — a greyed
    "VMAF (needs libvmaf)" explains the absence, where a short list would just look like the feature is
    missing.
    """
    combo.clear()
    for name, spec in QUALITY_METRICS.items():
        usable = metric_available(name)
        combo.addItem(spec['label'] if usable else f"{spec['label']} (needs {spec['filter']})", name)
        if not usable:
            index = combo.count() - 1
            combo.model().item(index).setEnabled(False)
