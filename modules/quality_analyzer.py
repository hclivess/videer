"""
Quality matching for videer

Answers one question: which CRF/CQ keeps *this* source looking the same, and no higher?

Picking a CRF by habit is a guess about content the number knows nothing about. A clean animated source is
still transparent at CRF 26 while a grainy film print is already falling apart at 20, so a fixed setting either
throws away quality or throws away disk space — and which of the two it did is invisible until the encode is
finished. The search here measures instead: it encodes a few short samples of the real file at candidate CRFs,
scores each against the source with a full-reference metric (VMAF, or SSIM where libvmaf is missing), and
bisects to the *highest* CRF that still meets the quality target. Highest, because among the settings that
preserve quality, the largest CRF is the smallest file.

Cost is bounded: bisecting a 20-step CRF range takes five probes, each of them a handful of seconds of video.
"""

import os
import re
import math
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from config import (DEFAULT_QUALITY_METRIC, DEFAULT_QUALITY_SEARCH_RANGE, QUALITY_METRICS,
                    QUALITY_SAMPLE_COUNT, QUALITY_SAMPLE_MARGIN, QUALITY_SAMPLE_SECONDS,
                    QUALITY_SEARCH_RANGE, VIDEO_EXTENSIONS)
from modules.process_manager import format_duration, format_size
from utils import childproc
from utils.ffmpeg_utils import (CRF_ENCODERS, FFmpegCommandBuilder, ffmpeg_has_filter,
                                find_ffmpeg, probe_media_info)


# How long to let a probe linger after it has closed its output before killing it. There is no stall watchdog
# on top of that, as there is for the queue: this runs behind a modal dialog whose Cancel button kills the
# process outright, so a wedged probe is always one click from gone.
PROBE_TIMEOUT = 30 * 60


def metric_available(metric: str) -> bool:
    """Whether this FFmpeg build has the filter that computes the metric"""
    spec = QUALITY_METRICS.get(metric)
    return bool(spec) and ffmpeg_has_filter(spec['filter'])


def choose_metric() -> str:
    """VMAF where the build can compute it, otherwise whatever it can — SSIM is always compiled in"""
    for metric in [DEFAULT_QUALITY_METRIC] + list(QUALITY_METRICS):
        if metric_available(metric):
            return metric
    return DEFAULT_QUALITY_METRIC


def search_range_for(video_codec: str) -> Tuple[int, int]:
    return QUALITY_SEARCH_RANGE.get(video_codec, DEFAULT_QUALITY_SEARCH_RANGE)


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


class QualityAnalyzer(QThread):
    """
    Runs the CRF search on a worker thread: probe encode + metric per sample, bisect on the result.

    Deliberately independent of ProcessManager. This is an interactive question about one file, answered while
    the queue is idle, and giving it its own thread keeps it out of the queue's state machine entirely.
    """

    progress = Signal(str)              # human-readable step
    step_advanced = Signal(int, int)    # steps done, steps expected
    probe_finished = Signal(dict)       # one CRF evaluated
    analysis_finished = Signal(dict)    # the recommendation
    failed = Signal(str)

    _METRIC_RE = {
        'vmaf': re.compile(r'VMAF score\s*[:=]\s*([\d.]+)', re.IGNORECASE),
        'ssim': re.compile(r'SSIM\b.*?\bAll\s*[:=]\s*([\d.]+)', re.IGNORECASE),
    }

    def __init__(self, filepath: str, settings: Dict[str, Any], metric: str, target: float,
                 sample_count: int, sample_seconds: float, crf_low: int, crf_high: int,
                 parent=None):
        super().__init__(parent)
        self.filepath = filepath
        self.settings = dict(settings)
        self.metric = metric
        self.target = float(target)
        self.sample_count = int(sample_count)
        self.sample_seconds = float(sample_seconds)
        self.crf_low = int(crf_low)
        self.crf_high = int(crf_high)

        self.should_stop = False
        self._proc: Optional[subprocess.Popen] = None
        self._workdir: Optional[str] = None
        self._steps_done = 0
        self._steps_expected = 1

    # ------------------------------------------------------------------
    def stop(self):
        """Cancel: the probe in flight is killed, the search unwinds on its own"""
        self.should_stop = True
        proc = self._proc
        if proc is not None:
            childproc.kill(proc)

    # ------------------------------------------------------------------
    def run(self):
        try:
            self._search()
        except Exception as exc:                              # noqa: BLE001 - a dialog must never lose the thread
            if not self.should_stop:
                self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            if self._workdir:
                shutil.rmtree(self._workdir, ignore_errors=True)
                self._workdir = None

    def _search(self):
        if not find_ffmpeg():
            self.failed.emit("FFmpeg was not found.")
            return

        info = probe_media_info(self.filepath)
        duration = info.get('duration')
        samples = plan_samples(duration, self.sample_count, self.sample_seconds)
        sample_total = sum(length for _, length in samples)

        # Bisection halves the range each time, so the number of probes is known before the first one runs
        span = max(1, self.crf_high - self.crf_low + 1)
        probes_expected = max(1, int(math.ceil(math.log2(span + 1))))
        self._steps_expected = probes_expected * len(samples) * 2
        self._steps_done = 0

        self.progress.emit(
            f"Source: {format_duration(duration)}, {format_size(info.get('size'))}"
            + (f", {info['width']}x{info['height']}" if info.get('width') else "")
            + (f", {info['codec']}" if info.get('codec') else ""))
        self.progress.emit(
            f"Measuring {len(samples)} sample(s) of {format_duration(sample_total / len(samples))} "
            f"at CRF {self.crf_low}–{self.crf_high} — about {probes_expected} probes.")

        self._workdir = tempfile.mkdtemp(prefix="videer-quality-")

        results: Dict[int, Dict[str, Any]] = {}
        low, high = self.crf_low, self.crf_high
        best: Optional[Dict[str, Any]] = None

        while low <= high and not self.should_stop:
            crf = (low + high) // 2
            probe = self._evaluate(crf, samples, info)
            if probe is None:
                return                                        # cancelled, or already reported as failed
            results[crf] = probe
            self.probe_finished.emit(probe)

            if probe['meets_target']:
                best = probe
                low = crf + 1                                 # quality to spare: try to save more space
            else:
                high = crf - 1                                # went too far: back off

        if self.should_stop:
            return

        self.analysis_finished.emit(self._summarize(best, results, info, samples))

    # ------------------------------------------------------------------
    def _evaluate(self, crf: int, samples: List[Tuple[float, float]],
                  info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Encode every sample at this CRF and score it — returns None if cancelled or the probe failed"""
        builder = FFmpegCommandBuilder({**self.settings, 'crf': crf})
        threads = int(self.settings.get('threads') or 0)

        scores: List[float] = []
        encoded_bytes = 0
        encoded_seconds = 0.0

        for number, (start, length) in enumerate(samples, start=1):
            if self.should_stop:
                return None
            label = f"CRF {crf} · sample {number}/{len(samples)} at {format_duration(start)}"

            sample_path = os.path.join(self._workdir, f"probe_crf{crf}_{number}.mkv")
            self.progress.emit(f"{label}: encoding…")
            command = builder.build_sample_encode_command(self.filepath, sample_path, start, length)
            if self._run(command) != 0:
                if self.should_stop:
                    return None
                self.failed.emit(f"The probe encode at CRF {crf} failed — see the log for FFmpeg's reason.")
                return None
            self._advance()

            try:
                encoded_bytes += os.path.getsize(sample_path)
            except OSError:
                pass
            encoded_seconds += length

            self.progress.emit(f"{label}: scoring…")
            score = self._measure(builder, sample_path, start, length, threads)
            if score is None:
                if self.should_stop:
                    return None
                self.failed.emit(
                    f"Could not read a {QUALITY_METRICS[self.metric]['label']} score for CRF {crf}. "
                    f"Check that this FFmpeg build has the "
                    f"{QUALITY_METRICS[self.metric]['filter']} filter.")
                return None
            scores.append(score)
            self._advance()

            try:
                os.remove(sample_path)
            except OSError:
                pass

        score = sum(scores) / len(scores)
        bitrate = (encoded_bytes * 8 / encoded_seconds) if encoded_seconds else None
        duration = info.get('duration')
        estimated = (bitrate * duration / 8) if (bitrate and duration) else None

        probe = {
            'crf': crf,
            'score': score,
            'scores': scores,
            'bitrate': bitrate,
            'estimated_size': estimated,
            'source_size': info.get('size'),
            'meets_target': score >= self.target,
        }
        self.progress.emit(
            f"CRF {crf}: {self._format_score(score)} "
            f"({'meets' if probe['meets_target'] else 'below'} target)"
            + (f", ≈{format_size(estimated)}" if estimated else ""))
        return probe

    def _measure(self, builder: FFmpegCommandBuilder, sample_path: str, start: float,
                 length: float, threads: int) -> Optional[float]:
        """Score one encoded sample against the same segment of the source"""
        pattern = self._METRIC_RE[self.metric]
        value: Optional[float] = None

        def on_line(line: str):
            nonlocal value
            match = pattern.search(line)
            if match:
                try:
                    value = float(match.group(1))
                except ValueError:
                    pass

        command = builder.build_metric_command(sample_path, self.filepath, self.metric,
                                               start=start, duration=length, threads=threads)
        self._run(command, on_line)
        return value

    def _advance(self):
        self._steps_done += 1
        self._steps_expected = max(self._steps_expected, self._steps_done)
        self.step_advanced.emit(self._steps_done, self._steps_expected)

    # ------------------------------------------------------------------
    def _run(self, command: List[str], on_line=None) -> int:
        """Run one FFmpeg step, feeding its output to on_line. Returns the exit code (1 when cancelled)."""
        try:
            proc = childproc.popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, universal_newlines=True,
                                   encoding='utf-8', errors='replace', bufsize=1)
        except OSError as exc:
            self.failed.emit(f"Could not start FFmpeg: {exc}")
            return 1

        self._proc = proc
        try:
            for raw_line in proc.stdout:
                if self.should_stop:
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

    # ------------------------------------------------------------------
    def _format_score(self, score: float) -> str:
        spec = QUALITY_METRICS[self.metric]
        return f"{spec['label']} {score:.{spec['decimals']}f}"

    def _summarize(self, best: Optional[Dict[str, Any]], results: Dict[int, Dict[str, Any]],
                   info: Dict[str, Any], samples: List[Tuple[float, float]]) -> Dict[str, Any]:
        """Turn the probes into a recommendation plus the caveats that go with it"""
        source_size = info.get('size')
        notes: List[str] = []

        if best is None:
            # Nothing in the range held the target. The closest attempt is the honest recommendation, and the
            # reason is almost always grain or noise: detail that costs a great many bits to reproduce.
            closest = max(results.values(), key=lambda p: p['score']) if results else None
            notes.append(
                f"No CRF in {self.crf_low}–{self.crf_high} reached the target. The source is probably grainy, "
                f"noisy or already heavily compressed — quality that expensive to keep is a sign that "
                f"re-encoding it will not save much.")
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

        if self.settings.get('use_avisynth'):
            notes.append("AviSynth+ processing is enabled but is not applied to the probes, so the real "
                         "encode will differ from this estimate.")
        if self.settings.get('reduce_fps') and self.settings.get('deinterlace'):
            notes.append("Halving the frame rate while deinterlacing changes the frame count, which the "
                         "metric cannot compare frame-for-frame; treat the score as approximate.")

        return {
            'file': self.filepath,
            'metric': self.metric,
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


class QualityMatchDialog(QDialog):
    """
    Interactive front end for the search: pick a file, pick how much quality has to survive, get the CRF.

    The encoder, speed preset and filter settings are taken from the main window as they stand — the point is
    to answer for the encode the user is actually about to run, not for a generic one.
    """

    SETTINGS_PREFIX = "quality_match/"

    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self.main_window = main_window
        self.setWindowTitle("Match Source Quality")
        self.setMinimumWidth(660)

        self.analyzer: Optional[QualityAnalyzer] = None
        self.result: Optional[Dict[str, Any]] = None
        self.metric = choose_metric()
        self._syncing_target = False

        self._build_ui()
        self._load_prefs()
        self._populate_files()
        self._sync_to_codec()      # last: it owns the CRF range, which is remembered per encoder

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
        spec = QUALITY_METRICS[self.metric]

        self.target_combo = QComboBox()
        for label, value in spec['targets']:
            self.target_combo.addItem(label, value)
        self.target_combo.addItem("Custom", None)
        self.target_combo.currentIndexChanged.connect(self._on_target_preset)

        self.target_spin = QDoubleSpinBox()
        self.target_spin.setRange(*spec['range'])
        self.target_spin.setDecimals(spec['decimals'])
        self.target_spin.setSingleStep(spec['step'])
        self.target_spin.setValue(spec['default_target'])
        self.target_spin.valueChanged.connect(self._on_target_value)

        target_row = QWidget()
        target_layout = QHBoxLayout(target_row)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.addWidget(self.target_combo, 1)
        target_layout.addWidget(QLabel(spec['label'] + ":"))
        target_layout.addWidget(self.target_spin)
        form.addRow("Quality to keep:", target_row)

        self.samples_spin = QSpinBox()
        self.samples_spin.setRange(1, 10)
        self.samples_spin.setValue(QUALITY_SAMPLE_COUNT)
        self.samples_spin.setToolTip(
            "How many places in the file to measure. More samples describe a varied source better;\n"
            "each one multiplies the time the search takes.")

        self.seconds_spin = QSpinBox()
        self.seconds_spin.setRange(2, 120)
        self.seconds_spin.setSuffix(" s")
        self.seconds_spin.setValue(QUALITY_SAMPLE_SECONDS)
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
        self.table.setHorizontalHeaderLabels(
            ["CRF", QUALITY_METRICS[self.metric]['label'], "Est. video", "vs source", "Verdict"])
        self.table.setToolTip(
            "Estimated size of the video stream over the whole file, scaled up from the samples.\n"
            "Audio, subtitles and container overhead come on top.")
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(140)
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
            f"{codec} · speed preset “{settings.get('preset')}” · "
            f"{QUALITY_METRICS[self.metric]['label']} as the quality metric")

        low, high = search_range_for(codec)
        saved_low = self._pref(f"crf_low_{codec}", 0, int)
        saved_high = self._pref(f"crf_high_{codec}", 0, int)
        # The range is remembered per encoder: AV1's CRF scale is not x265's, so carrying one over would
        # start the search in the wrong place entirely.
        self.crf_low_spin.setValue(saved_low or low)
        self.crf_high_spin.setValue(saved_high or high)

        searchable = codec in CRF_ENCODERS
        self.analyze_button.setEnabled(searchable)
        if not searchable:
            self.status_label.setText(
                f"‘{codec}’ has no CRF to search — pick x264, x265, AV1, VP9 or NVENC on the Video tab.")

    def _pref(self, key, default, cast):
        """One remembered dialog setting, falling back to the default on anything unreadable"""
        raw = self.main_window.settings.value(self.SETTINGS_PREFIX + key, default)
        try:
            return cast(raw)
        except (TypeError, ValueError):
            return default

    def _load_prefs(self):
        spec = QUALITY_METRICS[self.metric]
        self.target_spin.setValue(self._pref(f"target_{self.metric}", spec['default_target'], float))
        self.samples_spin.setValue(self._pref("samples", QUALITY_SAMPLE_COUNT, int))
        self.seconds_spin.setValue(self._pref("seconds", QUALITY_SAMPLE_SECONDS, int))
        self._match_target_preset()

    def _save_prefs(self):
        qsettings = self.main_window.settings
        codec = self.main_window.ui_manager.get_current_settings().get('video_codec')
        qsettings.setValue(self.SETTINGS_PREFIX + f"target_{self.metric}", self.target_spin.value())
        qsettings.setValue(self.SETTINGS_PREFIX + "samples", self.samples_spin.value())
        qsettings.setValue(self.SETTINGS_PREFIX + "seconds", self.seconds_spin.value())
        qsettings.setValue(self.SETTINGS_PREFIX + f"crf_low_{codec}", self.crf_low_spin.value())
        qsettings.setValue(self.SETTINGS_PREFIX + f"crf_high_{codec}", self.crf_high_spin.value())

    # ---- target combo/spin pair ---------------------------------------
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

        if not metric_available(self.metric):
            QMessageBox.warning(
                self, "Metric unavailable",
                f"This FFmpeg build has no {QUALITY_METRICS[self.metric]['filter']} filter, so quality "
                f"cannot be measured. Install an FFmpeg built with libvmaf.")
            return

        settings = self.main_window.ui_manager.get_current_settings()
        self.result = None
        self.table.setRowCount(0)
        self.result_label.setVisible(False)
        self.apply_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self._set_running(True)

        self.analyzer = QualityAnalyzer(
            filepath, settings, self.metric, self.target_spin.value(),
            self.samples_spin.value(), self.seconds_spin.value(),
            self.crf_low_spin.value(), self.crf_high_spin.value(), parent=self)
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
        self.analyze_button.setEnabled(not running and
                                       self.main_window.ui_manager.get_current_settings()
                                       .get('video_codec') in CRF_ENCODERS)
        self.cancel_button.setEnabled(running)
        for widget in (self.file_combo, self.target_combo, self.target_spin, self.samples_spin,
                       self.seconds_spin, self.crf_low_spin, self.crf_high_spin):
            widget.setEnabled(not running)
        if not running and self.analyzer and self.analyzer.should_stop:
            self.status_label.setText("Cancelled.")

    def _on_step(self, done: int, expected: int):
        self.progress_bar.setValue(int(min(100, done * 100 / max(1, expected))))

    def _on_probe(self, probe: Dict[str, Any]):
        """Add one measured CRF to the table, newest at the bottom, ordered by CRF"""
        spec = QUALITY_METRICS[self.metric]
        estimated = probe.get('estimated_size')
        source_size = probe.get('source_size')
        ratio = (f"{estimated / source_size * 100:.0f}%" if estimated and source_size else "--")

        row = self.table.rowCount()
        self.table.insertRow(row)
        for column, text in enumerate([
                str(probe['crf']),
                f"{probe['score']:.{spec['decimals']}f}",
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
        self.result = result
        spec = QUALITY_METRICS[self.metric]
        recommended = result.get('recommended')

        if not recommended:
            self.result_label.setText("No usable measurement — see the log above.")
            self.result_label.setStyleSheet("color: #a94442; font-weight: bold;")
            self.result_label.setVisible(True)
            return

        savings = result.get('savings')
        headline = (f"Recommended CRF {recommended['crf']} — "
                    f"{spec['label']} {recommended['score']:.{spec['decimals']}f}")
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
        self._save_prefs()

    def _on_failed(self, message: str):
        self.status_label.setText(message)
        self.result_label.setText(message)
        self.result_label.setStyleSheet("color: #a94442; font-weight: bold;")
        self.result_label.setVisible(True)

    def _on_apply(self):
        """Put the answer where the encode will use it: the CRF box on the Video tab"""
        if not self.result or not self.result.get('recommended'):
            return
        crf = int(self.result['recommended']['crf'])
        self.main_window.ui_manager.controls['crf'].setValue(crf)
        self.main_window.ui_manager.update_status(
            f"CRF set to {crf} from the quality match of "
            f"{os.path.basename(self.result['file'])}")
        self._save_prefs()
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
        self._save_prefs()
