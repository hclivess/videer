"""
Process Manager for videer
Handles FFmpeg process execution and monitoring
"""

import os
import sys
import time
import threading
import subprocess
import re
import shutil
import tempfile
import psutil
import shlex
from collections import deque
from typing import List, Dict, Any, Optional, Callable
from PySide6.QtCore import QThread, Signal, QObject, QMutex

from models.file_models import VideoFile, FAILURE_CONTEXT_LINES
from utils.ffmpeg_utils import FFmpegCommandBuilder, find_ffmpeg, probe_duration, CRF_ENCODERS
from modules.avisynth_handler import AviSynthHandler
from utils.file_utils import FileOperations
from utils import childproc
from config import CONTAINER_VIDEO_CODECS, CONTAINER_AUDIO_CODECS, DEFAULT_QUALITY_METRIC


# FFmpeg run with -progress pipe:1 reports continuously while it is working. Total silence for this long means
# the step is wedged (a known AviSynth+ MT failure mode), not slow — kill it rather than let it hold the queue
# and the CPU forever. Generous on purpose: a false positive costs the user an encode.
STALL_TIMEOUT = 30 * 60

# How long to let a process linger after it has closed its output before killing it.
EXIT_TIMEOUT = 60

# How long stop_processing() waits for the worker to unwind before letting it finish in the background.
STOP_GRACE_MS = 15000


def format_duration(seconds: Optional[float]) -> str:
    """Render seconds as 1h 02m 03s / 4m 05s / 12s; '--' when unknown"""
    if seconds is None or seconds < 0 or seconds != seconds:  # NaN guard
        return "--"
    seconds = int(round(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def format_size(num_bytes: Optional[float]) -> str:
    """Human-readable size"""
    if not num_bytes or num_bytes < 0:
        return "--"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024 or unit == "TB":
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return "--"


class ProcessThread(QThread):
    """Thread for processing video files"""

    # Pre-compiled regex patterns
    _DURATION_RE = re.compile(r'Duration: (\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?')
    # Wide on purpose: FFmpeg reports a fatal misconfiguration ("Unsupported channel layout", "Could not
    # write header") in words that none of the obvious three cover, and a missed line is a failure the user
    # cannot act on. Retention is bounded, so over-matching costs nothing but a longer report.
    _ERROR_KEYWORDS = ("error", "invalid", "failed", "unsupported", "not supported", "cannot",
                       "could not", "unable to", "no such", "denied", "does not contain")

    # Signals
    progress_signal = Signal(dict)          # structured progress snapshot
    info_signal = Signal(str)
    file_started = Signal(int)              # file index
    file_finished = Signal(int, bool)       # file index, success
    processing_finished = Signal(int, int)  # success count, total count
    vmaf_calculated = Signal(int, float, str)   # file index, score, metric
    crf_matched = Signal(int, int)          # file index, CRF the per-file search chose

    def __init__(self, process_manager, settings: Dict[str, Any]):
        super().__init__()
        self._pm = process_manager
        self.settings = settings
        self.should_stop = False
        self.paused = False
        self._pause_started: Optional[float] = None
        self.current_process: Optional[subprocess.Popen] = None
        self.current_pid: Optional[int] = None
        self._current_psutil: Optional[psutil.Process] = None
        self.start_time: Optional[float] = None
        self._phase_start: Optional[float] = None
        self.success_count = 0

        # Timing bookkeeping for ETA
        self._file_start_time: Optional[float] = None
        self._completed_wall_times: List[float] = []
        self._current_index = 0

        self.command_builder = FFmpegCommandBuilder(settings)
        self.avisynth_handler = AviSynthHandler(settings) if settings.get('use_avisynth') else None
        self.file_ops = FileOperations()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self):
        """
        Thread entry point. The queue loop lives in _run_queue(); this wrapper exists so that
        processing_finished is emitted on *every* exit path. It is the only signal that clears the app's
        "processing" state — if an unexpected error escaped here, the UI would stay locked in a run that has
        already ended, and closing the app would keep asking about a thread that is long dead.
        """
        try:
            self._run_queue()
        except BaseException as exc:                      # noqa: BLE001 - last line of defence for the thread
            try:
                self.info_signal.emit(f"Processing aborted: {type(exc).__name__}: {exc}")
            except Exception:
                pass
        finally:
            try:
                total = self._pm.get_total_file_count()
            except Exception:
                total = 0
            self.processing_finished.emit(self.success_count, total)

    def _run_queue(self):
        """Main processing loop — pulls files dynamically from the shared queue"""
        self.start_time = time.time()
        self.success_count = 0

        index = 0
        while not self.should_stop:
            # Paused between files: hold before pulling the next entry
            while self.paused and not self.should_stop:
                time.sleep(0.2)
            if self.should_stop:
                break

            file = self._pm.get_file_at(index)
            if file is None:
                break

            self._current_index = index
            self._pm.set_worker_index(index)
            self._file_start_time = time.time()
            total_count = self._pm.get_total_file_count()

            self.file_started.emit(index)
            self.info_signal.emit(f"Processing {index + 1}/{total_count}: {file.filename}")

            # Preparing a file can fail on its own (read-only directory, locked log, over-long Windows path,
            # a preset with a non-numeric size). That must fail this file, not kill the whole queue.
            file_settings = self.settings
            try:
                file.create_logger()
                file.duration = probe_duration(file.filepath)
                # The search has to happen before set_output_name: the CRF it picks is part of the output
                # filename, and naming the file after the queue's CRF would label every encode wrongly.
                file_settings = self._settings_for(file, index)
                file.set_output_name(file_settings)
                self.command_builder = FFmpegCommandBuilder(file_settings)
                prepared = True
            except Exception as exc:
                file.add_error(f"Could not prepare {file.filename}: {exc}")
                prepared = False

            success = self._process_file(file) if prepared else False

            if success:
                self.success_count += 1

                # Score before any file replacement (the original is the reference)
                if (self.settings.get('calculate_vmaf')
                        and self.settings.get('video_codec') != 'copy'):
                    self._verify_quality(file, file.get_full_output_path(), index)

                output_path = file.get_full_output_path()
                delete_source = bool(self.settings.get('delete_source'))
                if self.settings.get('replace_files'):
                    # With delete_source the .old backup is dropped as well. The encode keeps its own
                    # extension when the container differs from the source's — a .avi name holding Matroska
                    # is a file that lies about itself.
                    replaced = self.file_ops.replace_file_as(output_path, file.filepath, file.logger,
                                                             keep_backup=not delete_source)
                    if replaced is None:
                        self.info_signal.emit(f"{file.filename}: could not replace the original; "
                                              f"the encode is at {os.path.basename(output_path)}")
                else:
                    self.file_ops.preserve_timestamps(file.filepath, output_path, file.logger)
                    if delete_source:
                        # Free space as we go: remove this source right after its
                        # encode is verified, before moving on to the next file
                        if self.file_ops.delete_source(file.filepath, output_path, file.logger):
                            self.info_signal.emit(f"Deleted source: {file.filename}")

            try:
                file.cleanup_temp_files()
            except Exception as exc:
                self.info_signal.emit(f"Cleanup failed for {file.filename}: {exc}")
            self._completed_wall_times.append(time.time() - self._file_start_time)
            self.file_finished.emit(index, success)

            report = file.get_error_report()
            if report:
                self.info_signal.emit(f"Errors in {file.filename} ({file.error_count} total):\n" + report)
            # Retained error text has served its purpose; don't carry it for the rest of the batch
            file.clear_errors()

            index += 1

    def _process_file(self, file: VideoFile) -> bool:
        """Process a single file"""
        try:
            if self.settings.get('transcode_video') or self.settings.get('transcode_audio'):
                if not self._transcode(file):
                    return False
                input_file = file.transcode_name
            else:
                input_file = file.filepath

            if self.settings.get('use_avisynth') and self.avisynth_handler:
                if self.avisynth_handler.create_script(file):
                    input_file = file.avs_file
                else:
                    file.add_error("Failed to create AviSynth script")
                    return False

            temp_output = file.get_temp_output_path()
            final_output = file.get_full_output_path()
            command = self.command_builder.build_main_command(
                input_file, temp_output, self.settings.get('use_avisynth', False))

            return_code = self._execute_command(command, file, phase="Encoding")
            ok = return_code == 0 and not self.should_stop and self.file_ops.output_is_usable(temp_output)
            if ok:
                # only now does the file appear under its real name; a stopped / failed encode never does
                if os.path.exists(final_output):
                    os.remove(final_output)
                os.replace(temp_output, final_output)
            else:
                try:
                    if os.path.exists(temp_output):
                        os.remove(temp_output)
                except OSError:
                    pass
            return ok

        except Exception as e:
            file.add_error(f"Processing error: {str(e)}")
            return False

    def _transcode(self, file: VideoFile) -> bool:
        """Transcode to raw format"""
        file.log_info("Starting transcoding...")
        command = self.command_builder.build_transcode_command(
            file.filepath, file.transcode_name,
            self.settings.get('transcode_video', False),
            self.settings.get('transcode_audio', False))

        return_code = self._execute_command(command, file, phase="Transcoding")
        success = return_code == 0 and not self.should_stop
        if success:
            file.log_info("Transcoding completed successfully")
        else:
            file.add_error("Transcoding failed")
        return success

    # ------------------------------------------------------------------
    # Subprocess handling
    # ------------------------------------------------------------------
    @staticmethod
    def _format_command(command: List[str]) -> str:
        """Human-readable command line for logging"""
        if os.name == 'nt':
            return subprocess.list2cmdline(command)
        return ' '.join(shlex.quote(arg) for arg in command)

    def _start_subprocess(self, command: List[str], cwd: Optional[str] = None) -> subprocess.Popen:
        """Start FFmpeg without a shell so paths with spaces/quotes are passed verbatim"""
        kwargs = dict(
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            universal_newlines=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
            cwd=cwd,
        )
        if os.name == 'nt':
            # Plugins that pull in runtime DLLs via LoadLibrary (fft3dfilter ->
            # libfftw3f-3.dll) resolve them through PATH, so expose plugins/.
            if self.avisynth_handler:
                env = dict(os.environ)
                env['PATH'] = self.avisynth_handler.plugins_path + os.pathsep + env.get('PATH', '')
                kwargs['env'] = env
        return childproc.popen(command, **kwargs)

    @staticmethod
    def _to_float(value: str) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _run_monitored(self, command: List[str], file: VideoFile, phase: str,
                       duration: Optional[float],
                       on_line: Optional[Callable[[str], None]] = None,
                       cwd: Optional[str] = None) -> int:
        """
        Run an FFmpeg command started with `-progress pipe:1`, log its output and
        emit structured progress snapshots. Returns the return code (1 if stopped
        or failed to launch).
        """
        file.log_info(f"Executing: {self._format_command(command)}")

        try:
            process = self._start_subprocess(command, cwd=cwd)
        except Exception as e:
            file.add_error(f"Command execution error: {str(e)}")
            return 1

        # Keep a local reference: stop() runs on the GUI thread and clears
        # self.current_process while this loop is still draining stdout.
        self.current_process = process
        self.current_pid = process.pid

        # Everything from here runs under the try: once the child exists, no path may leave this method
        # without the finally having disposed of it.
        try:
            self._current_psutil = self._psutil_handle(process.pid)
            self._phase_start = time.time()
            if self.paused:  # pause hit while the process was being spawned
                self._signal_tree(suspend=True)

            # Reading FFmpeg's output happens on a helper thread, and this one becomes a watchdog. A blocking
            # read notices should_stop only when the next line arrives — which for a wedged encoder is never.
            # The interpreting work stays on the single reader thread so per-line cost is unchanged.
            state = {'last_output': time.time(), 'eof': False,
                     'tail': deque(maxlen=FAILURE_CONTEXT_LINES)}
            pump = threading.Thread(target=self._pump,
                                    args=(process, file, phase, duration, on_line, state),
                                    name="ffmpeg-reader", daemon=True)
            pump.start()

            stalled = False
            while not state['eof']:
                if self.should_stop:
                    self._kill_process()
                    return 1
                if self.paused:
                    state['last_output'] = time.time()   # a suspended encoder is not a stalled one
                elif STALL_TIMEOUT and time.time() - state['last_output'] > STALL_TIMEOUT:
                    stalled = True
                    break
                time.sleep(0.2)

            if stalled:
                minutes = int(STALL_TIMEOUT // 60)
                file.add_error(f"No output from FFmpeg for {minutes} minutes during {phase} — "
                               f"treating it as wedged and stopping it")
                self.info_signal.emit(f"{file.filename}: {phase} produced no output for {minutes} minutes; "
                                      f"stopping that step")
                self._kill_process()
                return 1

            pump.join(timeout=5)

            # A closed stdout does not mean the process exited: an AviSynth+ MT teardown can spin its worker
            # threads indefinitely after the last frame. Wait with a deadline, and never past a Stop.
            return_code = self._wait_with_deadline(process, file)
            if return_code != 0 and not self.should_stop:
                # The line that says why is usually not the line that says it failed
                file.note_failure_context(state['tail'])
        finally:
            # release(), never forget(): forgetting a still-running child hides it from both the Stop button
            # and kill_all(), leaving an encoder burning every core with nothing able to reach it.
            childproc.release(process)
            self.current_process = None
            self.current_pid = None
            self._current_psutil = None

        file.log_info(f"Process completed with return code: {return_code}")
        return return_code

    def _pump(self, process: subprocess.Popen, file: VideoFile, phase: str,
              duration: Optional[float], on_line: Optional[Callable[[str], None]], state: Dict[str, Any]):
        """
        Read and interpret FFmpeg's output until EOF. Runs on a helper thread so that _run_monitored can stay
        responsive to Stop and can notice a wedged process; the parsing itself is deliberately kept on this
        one thread rather than handed line-by-line to another, which costs several times more per line.

        `state` carries two things back: 'last_output' (the stall watchdog's clock) and 'eof'.
        """
        block: Dict[str, str] = {}
        last_emit = 0.0
        try:
            for raw_line in process.stdout:
                state['last_output'] = time.time()

                line = raw_line.strip()
                if not line:
                    continue

                # -progress key=value blocks, terminated by progress=continue|end
                if '=' in line and ' ' not in line.split('=', 1)[0]:
                    key, _, value = line.partition('=')
                    block[key] = value.strip()
                    if key == 'progress':
                        now = time.time()
                        if value.strip() == 'end' or now - last_emit >= 0.25:
                            self._emit_progress(file, phase, block, duration)
                            last_emit = now
                        block = {}
                    continue

                file.log_info(line)
                state['tail'].append(line)

                if duration is None:
                    match = self._DURATION_RE.search(line)
                    if match:
                        h, m, sec, frac = match.groups()
                        duration = int(h) * 3600 + int(m) * 60 + int(sec) + \
                            (float(f"0.{frac}") if frac else 0.0)

                if on_line:
                    on_line(line)
        except Exception as exc:
            # Never let this thread die quietly: the watchdog would wait out the full stall timeout.
            try:
                file.log_info(f"Output reader stopped: {type(exc).__name__}: {exc}")
            except Exception:
                pass
        finally:
            state['eof'] = True

    def _wait_with_deadline(self, process: subprocess.Popen, file: VideoFile) -> int:
        """
        Wait for the process to exit, re-checking should_stop, and give up on a process that will not go.
        Returns its return code, or 1 if it had to be killed.
        """
        deadline = time.time() + EXIT_TIMEOUT
        while True:
            if self.should_stop:
                self._kill_process()
                return 1
            try:
                return process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass
            if time.time() > deadline:
                file.add_error(f"FFmpeg closed its output but did not exit within {int(EXIT_TIMEOUT)}s "
                               f"— killing it")
                self.info_signal.emit(f"{file.filename}: FFmpeg would not exit; killed it")
                self._kill_process()
                return 1

    def _emit_progress(self, file: VideoFile, phase: str, block: Dict[str, str],
                       duration: Optional[float]):
        """Turn a -progress block into a snapshot dict for the UI"""
        phase_start = self._phase_start or time.time()
        out_time = None
        us = self._to_float(block.get('out_time_us') or block.get('out_time_ms'))
        if us is not None:
            out_time = us / 1_000_000.0

        speed = self._to_float((block.get('speed') or '').rstrip('x'))
        fps = self._to_float(block.get('fps'))
        bitrate = (block.get('bitrate') or '').strip()
        size = self._to_float(block.get('total_size'))
        frame = block.get('frame')

        percent = None
        eta_file = None
        elapsed = time.time() - phase_start
        if duration and out_time is not None and duration > 0:
            percent = max(0.0, min(100.0, out_time / duration * 100.0))
            remaining_media = max(0.0, duration - out_time)
            if speed and speed > 0:
                eta_file = remaining_media / speed
            elif percent > 0:
                eta_file = elapsed * (100.0 - percent) / percent

        if block.get('progress') == 'end':
            percent = 100.0
            eta_file = 0.0

        # Queue-level ETA: current file remainder + average wall time × files left
        total_files = self._pm.get_total_file_count()
        files_left = max(0, total_files - self._current_index - 1)
        if self._completed_wall_times:
            avg = sum(self._completed_wall_times) / len(self._completed_wall_times)
        elif percent and percent > 0:
            avg = (time.time() - self._file_start_time) * 100.0 / percent
        else:
            avg = None
        eta_total = None
        if eta_file is not None and (avg is not None or files_left == 0):
            eta_total = eta_file + files_left * (avg or 0.0)

        self.progress_signal.emit({
            'file_index': self._current_index,
            'total_files': total_files,
            'file_name': file.filename,
            'phase': phase,
            'percent': percent,
            'fps': fps,
            'speed': speed,
            'bitrate': bitrate if bitrate and bitrate != 'N/A' else None,
            'size': size,
            'frame': frame,
            'out_time': out_time,
            'duration': duration,
            'eta_file': eta_file,
            'eta_total': eta_total,
            'elapsed_file': time.time() - (self._file_start_time or phase_start),
            'elapsed_total': time.time() - (self.start_time or phase_start),
        })

    def _execute_command(self, command: List[str], file: VideoFile, phase: str) -> int:
        """Execute FFmpeg command, monitor progress and collect error lines"""
        def on_line(line):
            lower = line.lower()
            if any(keyword in lower for keyword in self._ERROR_KEYWORDS):
                file.add_error(line)

        return self._run_monitored(command, file, phase, file.duration, on_line)

    # ------------------------------------------------------------------
    # Quality: per-file CRF matching and post-encode verification
    # ------------------------------------------------------------------
    def _adopt_process(self, process: Optional[subprocess.Popen]):
        """
        Let the quality search's FFmpeg be the queue's current process while it runs, so Stop kills it and
        Pause suspends it. Without this a search would be unreachable by both — and a probe encode is exactly
        as expensive to leave running as a real one.
        """
        self.current_process = process
        self.current_pid = process.pid if process is not None else None
        self._current_psutil = self._psutil_handle(process.pid) if process is not None else None
        if process is not None and self.paused:
            self._signal_tree(suspend=True)

    def _settings_for(self, file: VideoFile, index: int) -> Dict[str, Any]:
        """
        The settings this particular file is encoded with. Identical to the queue's unless per-file quality
        matching is on, in which case the CRF is the one this file's own search asked for.
        """
        if not self.settings.get('auto_match_quality'):
            return self.settings
        if self.settings.get('video_codec') not in CRF_ENCODERS:
            return self.settings
        if self.should_stop:
            return self.settings

        crf = self._match_quality(file, index)
        return self.settings if crf is None else {**self.settings, 'crf': crf}

    def _match_quality(self, file: VideoFile, index: int) -> Optional[int]:
        """
        Search this file for the CRF it needs. Returns None to fall back to the queue's CRF — a search that
        cannot answer must not fail the file, because the fallback still produces a perfectly good encode.
        """
        # Deferred import: quality_analyzer takes its formatters from this module, so importing it at the
        # top would be a cycle.
        from modules.quality_analyzer import QualitySearch, format_score

        file.log_info("Quality match: searching for this file's own CRF")
        self.info_signal.emit(f"Matching quality for {file.filename}…")
        self._phase_start = time.time()

        # The panel is driven from both callbacks, not just the step counter: a step only completes when a
        # whole probe encode does, and on a slow preset with long samples that is minutes of a window that
        # looks frozen. Every message the search emits moves the phase pill and keeps the percentage honest.
        steps = {'done': 0, 'expected': 1}

        def on_progress(message: str):
            file.log_info(f"[quality] {message}")
            self.info_signal.emit(f"{file.filename}: {message}")
            self._emit_search_progress(file, steps['done'], steps['expected'])

        def on_step(done: int, expected: int):
            steps['done'], steps['expected'] = done, expected
            self._emit_search_progress(file, done, expected)

        self._emit_search_progress(file, 0, 1)

        search = QualitySearch(
            file.filepath, self.settings,
            on_progress=on_progress,
            on_step=on_step,
            should_stop=lambda: self.should_stop,
            on_process=self._adopt_process)

        result = search.run()

        if result is None:
            if not self.should_stop:
                reason = search.error or "no result"
                file.log_info(f"Quality match did not finish ({reason}); "
                              f"using the queue's CRF {self.settings.get('crf')}")
                self.info_signal.emit(f"{file.filename}: quality match failed ({reason}) — "
                                      f"encoding at CRF {self.settings.get('crf')}")
            return None

        recommended = result.get('recommended')
        if not recommended:
            return None

        crf = int(recommended['crf'])
        file.matched_crf = crf
        summary = (f"Quality match: CRF {crf} at "
                   f"{format_score(result['metric'], recommended['score'])} "
                   f"(target {result['target']:g}, pooled by {result['pool']})")
        if result.get('estimated_size'):
            summary += f", estimated {format_size(result['estimated_size'])} of video"
        if not result.get('reached_target'):
            summary += " — target not reached, this is the closest the range allowed"
        file.log_info(summary)
        for note in result.get('notes', []):
            file.log_info(f"[quality] {note}")
        self.info_signal.emit(f"{file.filename}: {summary}")
        self.crf_matched.emit(index, crf)
        return crf

    def _emit_search_progress(self, file: VideoFile, done: int, expected: int):
        """Give the progress panel something true to show while a search runs — it is not a quick step"""
        self.progress_signal.emit({
            'file_index': self._current_index,
            'total_files': self._pm.get_total_file_count(),
            'file_name': file.filename,
            'phase': 'Quality match',
            'percent': min(100.0, done * 100.0 / max(1, expected)),
            'fps': None, 'speed': None, 'bitrate': None, 'size': None, 'frame': None,
            'out_time': None, 'duration': None, 'eta_file': None, 'eta_total': None,
            'elapsed_file': time.time() - (self._file_start_time or time.time()),
            'elapsed_total': time.time() - (self.start_time or time.time()),
        })

    def _verify_quality(self, file: VideoFile, encoded_path: str, file_index: int):
        """Score the finished encode against the original with the chosen metric and pooling"""
        from modules.quality_analyzer import (LOG_NAME, VERIFY_PREFIX, choose_metric, format_score,
                                              metric_spec, parse_metric_log, parse_metric_summary,
                                              pool_scores)

        metric = choose_metric(self.settings.get('quality_metric', DEFAULT_QUALITY_METRIC))
        pool = self.settings.get('quality_pool', 'mean')
        label = metric_spec(metric)['label']
        file.log_info(f"Starting {label} verification...")

        workdir = tempfile.mkdtemp(prefix=VERIFY_PREFIX)
        lines: List[str] = []
        command = self.command_builder.build_metric_command(
            encoded_path, file.filepath, metric,
            threads=int(self.settings.get('threads') or 0), log_name=LOG_NAME)

        try:
            # cwd, not an absolute log path: a filter option carrying a Windows path would have to escape
            # both the drive colon and every backslash for the filtergraph parser.
            self._run_monitored(command, file, label, file.duration, lines.append, cwd=workdir)
            frames = parse_metric_log(os.path.join(workdir, LOG_NAME), metric)
            score = pool_scores(frames, pool) if frames else parse_metric_summary(lines, metric)
        except Exception as e:
            file.log_info(f"{label} verification failed: {str(e)}")
            return
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        if score is not None:
            file.vmaf_score = score
            file.quality_metric = metric
            file.log_info(f"{format_score(metric, score)} over {len(frames)} frames "
                          f"(pooled by {pool})" if frames else f"{format_score(metric, score)}")
            self.vmaf_calculated.emit(file_index, score, metric)
        else:
            file.log_info(f"{label} score could not be read from the output")

    def stop(self):
        """Stop processing"""
        self.should_stop = True
        if self.paused:
            self._signal_tree(suspend=False)
            self.paused = False
        self._kill_process()

    def pause(self):
        """Suspend the running FFmpeg process tree and hold the queue"""
        if self.paused or self.should_stop:
            return
        self.paused = True
        self._pause_started = time.time()
        self._signal_tree(suspend=True)

    def resume(self):
        """Resume a paused FFmpeg process tree; shift ETA clocks past the gap"""
        if not self.paused:
            return
        self._signal_tree(suspend=False)
        if self._pause_started is not None:
            delta = time.time() - self._pause_started
            if self.start_time is not None:
                self.start_time += delta
            if self._file_start_time is not None:
                self._file_start_time += delta
            if self._phase_start is not None:
                self._phase_start += delta
        self._pause_started = None
        self.paused = False

    @staticmethod
    def _psutil_handle(pid: int) -> Optional[psutil.Process]:
        """
        psutil.Process identifies a process by pid *and* creation time, so a handle taken when we spawned the
        child can never be confused with an unrelated process that later inherits the same pid. Looking the pid
        up again at pause time can be — and suspending a random system process is its own kind of hang.
        """
        try:
            return psutil.Process(pid)
        except psutil.Error:
            return None

    def _signal_tree(self, suspend: bool):
        """Suspend or resume the current FFmpeg process and its children"""
        parent = self._current_psutil
        if parent is None:
            return
        try:
            if not parent.is_running():
                return
            for proc in [parent] + parent.children(recursive=True):
                try:
                    proc.suspend() if suspend else proc.resume()
                except psutil.Error:
                    pass
        except psutil.Error:
            pass

    def _kill_process(self):
        """Kill current FFmpeg process and children"""
        process = self.current_process
        if process is not None:
            childproc.kill(process)
        self.current_pid = None
        self.current_process = None
        self._current_psutil = None


class ProcessManager(QObject):
    """Manages video processing operations"""

    # Signals
    progress_updated = Signal(int, int)        # overall percentage (0-100), maximum (100)
    status_updated = Signal(str)               # short human-readable status text
    stats_updated = Signal(dict)               # rich progress snapshot for the UI panel
    file_state_changed = Signal(int, str)      # index, 'running' | 'success' | 'failed'
    vmaf_calculated = Signal(int, float, str)  # index, score, metric
    crf_matched = Signal(int, int)             # index, matched CRF
    processing_finished = Signal(int, int)     # success count, total count
    paused_state_changed = Signal(bool)        # True = paused

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.process_thread: Optional[ProcessThread] = None
        self._is_processing = False
        self._current_file_index = 0
        self._total_files = 0
        self._queue_mutex = QMutex()
        self._shared_files: List[VideoFile] = []
        self._worker_index = -1

    # ---- shared queue --------------------------------------------------
    def set_worker_index(self, index: int):
        """
        Called from the worker thread as it picks up each file. This — not the GUI-side
        _current_file_index — is the authority for where the queue may be edited: _current_file_index is
        written by a queued slot, so any modal dialog freezes it, and splicing against a stale value pushes
        already-encoded files back into the pending tail to be encoded a second time.
        """
        self._queue_mutex.lock()
        try:
            self._worker_index = index
        finally:
            self._queue_mutex.unlock()

    def sync_pending(self, files: List[VideoFile]):
        """
        Thread-safe: make the not-yet-started tail of the running queue match
        the UI queue (handles files added *and* removed while encoding).
        Files up to and including the current one are never touched.
        """
        self._queue_mutex.lock()
        try:
            keep = self._worker_index + 1
            self._shared_files = self._shared_files[:keep] + list(files[keep:])
            self._total_files = len(self._shared_files)
        finally:
            self._queue_mutex.unlock()

    def get_file_at(self, index: int) -> Optional[VideoFile]:
        """Thread-safe: retrieve file at index, or None if out of range."""
        self._queue_mutex.lock()
        try:
            return self._shared_files[index] if index < len(self._shared_files) else None
        finally:
            self._queue_mutex.unlock()

    def get_total_file_count(self) -> int:
        """Thread-safe: get current total file count."""
        self._queue_mutex.lock()
        try:
            return len(self._shared_files)
        finally:
            self._queue_mutex.unlock()

    @property
    def current_file_index(self) -> int:
        """Where the worker actually is — safe to compare removal indices against."""
        self._queue_mutex.lock()
        try:
            return self._worker_index
        finally:
            self._queue_mutex.unlock()

    # ---- lifecycle -----------------------------------------------------
    def start_processing(self, files: List[VideoFile], settings: Dict[str, Any]):
        """Start processing files with given settings"""
        if self._is_processing:
            return

        # A previous run whose worker has not finished unwinding still owns the shared queue and may still
        # have an FFmpeg alive. Starting now would put two threads on the same queue, encoding into the same
        # .part path.
        if self.process_thread is not None and self.process_thread.isRunning():
            self.status_updated.emit("Previous run is still stopping — try again in a moment")
            return

        if not find_ffmpeg():
            self.status_updated.emit("Error: FFmpeg not found!")
            return

        self._current_file_index = 0
        self._queue_mutex.lock()
        try:
            self._shared_files = list(files)
            self._total_files = len(self._shared_files)
            self._worker_index = -1
        finally:
            self._queue_mutex.unlock()

        self.process_thread = ProcessThread(self, settings)
        self.process_thread.progress_signal.connect(self._on_progress)
        self.process_thread.info_signal.connect(self._on_info)
        self.process_thread.file_started.connect(self._on_file_started)
        self.process_thread.file_finished.connect(self._on_file_finished)
        self.process_thread.processing_finished.connect(self._on_processing_finished)
        self.process_thread.vmaf_calculated.connect(self.vmaf_calculated)
        self.process_thread.crf_matched.connect(self.crf_matched)

        self._is_processing = True
        self.process_thread.start()

        self.progress_updated.emit(0, 100)
        self.status_updated.emit("Processing started...")

    def pause_processing(self):
        """Pause: suspend FFmpeg and hold the queue after the current point"""
        if self.process_thread and self.process_thread.isRunning() and not self.is_paused():
            self.process_thread.pause()
            self.paused_state_changed.emit(True)
            self.status_updated.emit("Paused")

    def resume_processing(self):
        """Resume a paused run"""
        if self.process_thread and self.process_thread.isRunning() and self.is_paused():
            self.process_thread.resume()
            self.paused_state_changed.emit(False)
            self.status_updated.emit("Resumed")

    def is_paused(self) -> bool:
        return bool(self.process_thread and self.process_thread.paused)

    def stop_processing(self):
        """
        Ask the current run to stop and wait for it to actually unwind.

        Deliberately no QThread.terminate(): terminate() returns before the thread has stopped (isRunning()
        is still true on the next line) and on a Python worker it can land mid-bytecode, leaving the GIL or
        childproc's lock held — which freezes the GUI while FFmpeg keeps running. Nor is _is_processing
        cleared here: run() always emits processing_finished, and that is what ends the run. If the worker
        needs longer than the grace period, it is left to finish in the background and the app stays honest
        about still being busy.
        """
        if not (self.process_thread and self.process_thread.isRunning()):
            return

        self.paused_state_changed.emit(False)
        self.status_updated.emit("Stopping…")
        self.process_thread.stop()

        if not self.process_thread.wait(STOP_GRACE_MS):
            self.status_updated.emit(
                "Still finishing the current step — the run will stop as soon as it can")

    def is_processing(self) -> bool:
        return self._is_processing

    # ---- slots ---------------------------------------------------------
    def _overall_percent(self, file_percent: float) -> int:
        if self._total_files == 0:
            return 0
        overall = (self._current_file_index + file_percent / 100.0) / self._total_files
        return int(max(0.0, min(100.0, overall * 100.0)))

    def _on_progress(self, snapshot: Dict[str, Any]):
        percent = snapshot.get('percent')
        overall = self._overall_percent(percent or 0.0)
        snapshot = dict(snapshot, overall_percent=overall)
        self.stats_updated.emit(snapshot)
        self.progress_updated.emit(overall, 100)

        # Compact one-line status
        pct = f"{percent:.0f}%" if percent is not None else "…"
        eta = format_duration(snapshot.get('eta_file'))
        self.status_updated.emit(
            f"[{snapshot['file_index'] + 1}/{snapshot['total_files']}] "
            f"{snapshot['phase']} {snapshot['file_name']} — {pct}, ETA {eta}")

    def _on_info(self, message: str):
        # A PyInstaller --windowed build has no stdout on Windows (sys.stdout is None), and a bare print()
        # there raises inside a Qt slot. Fall back to the status line, which the user can actually see.
        stream = sys.stdout
        if stream is not None:
            try:
                stream.write(f"[INFO] {message}\n")
                stream.flush()
                return
            except Exception:
                pass
        self.status_updated.emit(message.splitlines()[0] if message else "")

    def _on_file_started(self, index: int):
        self._current_file_index = index
        self.file_state_changed.emit(index, 'running')
        self.progress_updated.emit(self._overall_percent(0), 100)

    def _on_file_finished(self, index: int, success: bool):
        self.file_state_changed.emit(index, 'success' if success else 'failed')
        self.progress_updated.emit(self._overall_percent(100), 100)

    def _on_processing_finished(self, success_count: int, total_count: int):
        self._is_processing = False
        self.paused_state_changed.emit(False)
        # Drop the run's own copy of the queue; the UI and FileManager still hold what the user can see.
        self._queue_mutex.lock()
        try:
            self._shared_files = []
            self._worker_index = -1
        finally:
            self._queue_mutex.unlock()
        self.status_updated.emit(f"Completed: {success_count}/{total_count} files processed successfully")
        self.processing_finished.emit(success_count, total_count)
        self.progress_updated.emit(100, 100)

    # ---- validation ----------------------------------------------------
    @staticmethod
    def validate_settings(settings: Dict[str, Any]) -> List[str]:
        """Return a list of human-readable warnings about the chosen settings"""
        issues = []
        video_codec = settings.get('video_codec')
        audio_codec = settings.get('audio_codec')
        output_format = (settings.get('output_format') or '').lower()

        allowed_v = CONTAINER_VIDEO_CODECS.get(output_format)
        if allowed_v and video_codec not in allowed_v:
            issues.append(f"{output_format.upper()} only supports VP9/AV1 video — "
                          f"'{video_codec}' will fail. Choose MKV/MP4 or switch codec.")
        allowed_a = CONTAINER_AUDIO_CODECS.get(output_format)
        if allowed_a and audio_codec not in allowed_a:
            issues.append(f"{output_format.upper()} only supports Opus audio — "
                          f"'{audio_codec}' will fail.")

        if video_codec == 'prores_ks' and output_format not in ('mov', 'mkv'):
            issues.append("ProRes works best in MOV or MKV containers.")
        if video_codec == 'rawvideo' and output_format == 'mp4':
            issues.append("Raw video cannot be stored in MP4; use AVI/MKV/MOV.")
        if audio_codec == 'pcm_s32le' and output_format == 'mp4':
            issues.append("MP4 does not support PCM audio; use MOV/MKV or a lossy codec.")

        if settings.get('use_avisynth'):
            if not sys.platform.startswith('win'):
                issues.append("AviSynth+ is only available on Windows; disable it or use bwdif/yadif.")
            missing = AviSynthHandler(settings).get_missing_plugins()
            if missing:
                issues.append("Missing AviSynth plugins in plugins/: " + ", ".join(missing))

        if settings.get('deinterlace'):
            if settings.get('deinterlacer', 'qtgmc') == 'qtgmc':
                if not settings.get('use_avisynth'):
                    issues.append("QTGMC deinterlacing requires AviSynth+ to be enabled.")
                elif not settings.get('use_ffms2'):
                    issues.append("QTGMC deinterlacing needs the FFMS2 source filter for non-AVI inputs.")
            if video_codec == 'copy':
                issues.append("Deinterlacing has no effect when the video stream is copied.")

        if video_codec == 'copy':
            res_mode = settings.get('resolution_mode') or ''
            if res_mode and not res_mode.startswith('Original'):
                issues.append("Resolution scaling is ignored when the video stream is copied.")

        if settings.get('stereo') and audio_codec == 'copy':
            issues.append("Force Stereo has no effect when the audio stream is copied.")
        if output_format in ('avi', 'webm'):
            issues.append(f"{output_format.upper()} cannot carry text subtitles — they will be dropped.")
        if settings.get('transcode_video') or settings.get('transcode_audio'):
            issues.append("Raw pre-transcode uses an AVI intermediate: subtitles are not carried over.")
        if settings.get('delete_source'):
            issues.append("Delete Source Files is ON: each original is permanently deleted "
                          "as soon as its encode succeeds (no .old backup, no recycle bin).")
        measuring = settings.get('calculate_vmaf') or settings.get('auto_match_quality')
        if measuring and settings.get('deinterlace') and settings.get('reduce_fps'):
            issues.append("Quality metrics compare frame for frame; halving FPS while deinterlacing changes "
                          "the frame count and will make the comparison fail.")
        if settings.get('auto_match_quality'):
            if video_codec not in CRF_ENCODERS:
                issues.append(f"Automatic CRF matching needs a CRF-based encoder; '{video_codec}' has no CRF, "
                              f"so every file will use the queue's setting instead.")
            elif settings.get('use_avisynth'):
                issues.append("Automatic CRF matching samples the source directly, without the AviSynth+ "
                              "chain, so the CRF it picks is measured on different frames than the encode.")

        return issues
