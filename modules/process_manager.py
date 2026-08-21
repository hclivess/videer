"""
Process Manager for videer
Handles FFmpeg process execution and monitoring
"""

import os
import sys
import time
import subprocess
import re
import psutil
import shlex
from typing import List, Dict, Any, Optional, Callable
from PySide6.QtCore import QThread, Signal, QObject, QMutex

from models.file_models import VideoFile
from utils.ffmpeg_utils import FFmpegCommandBuilder, find_ffmpeg, probe_duration
from modules.avisynth_handler import AviSynthHandler
from utils.file_utils import FileOperations
from config import CONTAINER_VIDEO_CODECS, CONTAINER_AUDIO_CODECS


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
    _VMAF_RE = re.compile(r'VMAF score\s*[:=]\s*([\d.]+)', re.IGNORECASE)
    _ERROR_KEYWORDS = ("error", "invalid", "failed")

    # Signals
    progress_signal = Signal(dict)          # structured progress snapshot
    info_signal = Signal(str)
    file_started = Signal(int)              # file index
    file_finished = Signal(int, bool)       # file index, success
    processing_finished = Signal(int, int)  # success count, total count
    vmaf_calculated = Signal(int, float)    # file index, score

    def __init__(self, process_manager, settings: Dict[str, Any]):
        super().__init__()
        self._pm = process_manager
        self.settings = settings
        self.should_stop = False
        self.paused = False
        self._pause_started: Optional[float] = None
        self.current_process: Optional[subprocess.Popen] = None
        self.current_pid: Optional[int] = None
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
            self._file_start_time = time.time()
            total_count = self._pm.get_total_file_count()

            file.create_logger()
            file.set_output_name(self.settings)
            file.duration = probe_duration(file.filepath)

            self.file_started.emit(index)
            self.info_signal.emit(f"Processing {index + 1}/{total_count}: {file.filename}")

            success = self._process_file(file)

            if success:
                self.success_count += 1

                # Calculate VMAF before any file replacement (needs original as reference)
                if (self.settings.get('calculate_vmaf')
                        and self.settings.get('video_codec') != 'copy'):
                    self._calculate_vmaf(file, file.get_full_output_path(), index)

                output_path = file.get_full_output_path()
                delete_source = bool(self.settings.get('delete_source'))
                if self.settings.get('replace_files'):
                    # With delete_source the .old backup is dropped as well
                    self.file_ops.replace_file(output_path, file.filepath, file.logger,
                                               keep_backup=not delete_source)
                else:
                    self.file_ops.preserve_timestamps(file.filepath, output_path, file.logger)
                    if delete_source:
                        # Free space as we go: remove this source right after its
                        # encode is verified, before moving on to the next file
                        if self.file_ops.delete_source(file.filepath, output_path, file.logger):
                            self.info_signal.emit(f"Deleted source: {file.filename}")

            file.cleanup_temp_files()
            self._completed_wall_times.append(time.time() - self._file_start_time)
            self.file_finished.emit(index, success)

            if file.error_messages:
                self.info_signal.emit(f"Errors in {file.filename}:\n" + '\n'.join(file.error_messages))

            index += 1

        self.processing_finished.emit(self.success_count, self._pm.get_total_file_count())

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

    def _start_subprocess(self, command: List[str]) -> subprocess.Popen:
        """Start FFmpeg without a shell so paths with spaces/quotes are passed verbatim"""
        kwargs = dict(
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            universal_newlines=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
        )
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            # Plugins that pull in runtime DLLs via LoadLibrary (fft3dfilter ->
            # libfftw3f-3.dll) resolve them through PATH, so expose plugins/.
            if self.avisynth_handler:
                env = dict(os.environ)
                env['PATH'] = self.avisynth_handler.plugins_path + os.pathsep + env.get('PATH', '')
                kwargs['env'] = env
        return subprocess.Popen(command, **kwargs)

    @staticmethod
    def _to_float(value: str) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _run_monitored(self, command: List[str], file: VideoFile, phase: str,
                       duration: Optional[float],
                       on_line: Optional[Callable[[str], None]] = None) -> int:
        """
        Run an FFmpeg command started with `-progress pipe:1`, log its output and
        emit structured progress snapshots. Returns the return code (1 if stopped
        or failed to launch).
        """
        file.log_info(f"Executing: {self._format_command(command)}")

        try:
            process = self._start_subprocess(command)
        except Exception as e:
            file.add_error(f"Command execution error: {str(e)}")
            return 1

        # Keep a local reference: stop() runs on the GUI thread and clears
        # self.current_process while this loop is still draining stdout.
        self.current_process = process
        self.current_pid = process.pid
        self._phase_start = time.time()
        if self.paused:  # pause hit while the process was being spawned
            self._signal_tree(suspend=True)
        block: Dict[str, str] = {}
        last_emit = 0.0

        try:
            for raw_line in process.stdout:
                if self.should_stop:
                    self._kill_process()
                    return 1

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

                if duration is None:
                    match = self._DURATION_RE.search(line)
                    if match:
                        h, m, sec, frac = match.groups()
                        duration = int(h) * 3600 + int(m) * 60 + int(sec) + \
                            (float(f"0.{frac}") if frac else 0.0)

                if on_line:
                    on_line(line)

            return_code = process.wait()
        finally:
            self.current_process = None
            self.current_pid = None

        file.log_info(f"Process completed with return code: {return_code}")
        return return_code

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

    def _calculate_vmaf(self, file: VideoFile, encoded_path: str, file_index: int):
        """Calculate VMAF score by comparing encoded file against original"""
        file.log_info("Starting VMAF calculation...")
        command = self.command_builder.build_vmaf_command(encoded_path, file.filepath)
        vmaf_score: Optional[float] = None

        def on_line(line):
            nonlocal vmaf_score
            match = self._VMAF_RE.search(line)
            if match:
                vmaf_score = float(match.group(1))

        try:
            self._run_monitored(command, file, "VMAF", file.duration, on_line)
        except Exception as e:
            file.log_info(f"VMAF calculation failed: {str(e)}")
            return

        if vmaf_score is not None:
            file.vmaf_score = vmaf_score
            file.log_info(f"VMAF score: {vmaf_score}")
            self.vmaf_calculated.emit(file_index, vmaf_score)
        else:
            file.log_info("VMAF score could not be parsed from output")

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

    def _signal_tree(self, suspend: bool):
        """Suspend or resume the current FFmpeg process and its children"""
        if not self.current_pid:
            return
        try:
            parent = psutil.Process(self.current_pid)
            for proc in [parent] + parent.children(recursive=True):
                try:
                    proc.suspend() if suspend else proc.resume()
                except psutil.NoSuchProcess:
                    pass
        except psutil.NoSuchProcess:
            pass

    def _kill_process(self):
        """Kill current FFmpeg process and children"""
        if not self.current_pid:
            return
        try:
            parent = psutil.Process(self.current_pid)
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            parent.kill()
        except psutil.NoSuchProcess:
            pass
        finally:
            self.current_pid = None
            self.current_process = None


class ProcessManager(QObject):
    """Manages video processing operations"""

    # Signals
    progress_updated = Signal(int, int)        # overall percentage (0-100), maximum (100)
    status_updated = Signal(str)               # short human-readable status text
    stats_updated = Signal(dict)               # rich progress snapshot for the UI panel
    file_state_changed = Signal(int, str)      # index, 'running' | 'success' | 'failed'
    vmaf_calculated = Signal(int, float)       # index, score
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

    # ---- shared queue --------------------------------------------------
    def sync_pending(self, files: List[VideoFile]):
        """
        Thread-safe: make the not-yet-started tail of the running queue match
        the UI queue (handles files added *and* removed while encoding).
        Files up to and including the current one are never touched.
        """
        self._queue_mutex.lock()
        try:
            keep = self._current_file_index + 1
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
        return self._current_file_index

    # ---- lifecycle -----------------------------------------------------
    def start_processing(self, files: List[VideoFile], settings: Dict[str, Any]):
        """Start processing files with given settings"""
        if self._is_processing:
            return

        if not find_ffmpeg():
            self.status_updated.emit("Error: FFmpeg not found!")
            return

        self._current_file_index = 0
        self._queue_mutex.lock()
        try:
            self._shared_files = list(files)
            self._total_files = len(self._shared_files)
        finally:
            self._queue_mutex.unlock()

        self.process_thread = ProcessThread(self, settings)
        self.process_thread.progress_signal.connect(self._on_progress)
        self.process_thread.info_signal.connect(self._on_info)
        self.process_thread.file_started.connect(self._on_file_started)
        self.process_thread.file_finished.connect(self._on_file_finished)
        self.process_thread.processing_finished.connect(self._on_processing_finished)
        self.process_thread.vmaf_calculated.connect(self.vmaf_calculated)

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
        """Stop current processing"""
        if self.process_thread and self.process_thread.isRunning():
            self.process_thread.stop()
            self.process_thread.wait(5000)
            if self.process_thread.isRunning():
                self.process_thread.terminate()
            self._is_processing = False
            self.paused_state_changed.emit(False)
            self.status_updated.emit("Processing stopped")

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
        print(f"[INFO] {message}")

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
        if settings.get('calculate_vmaf') and settings.get('deinterlace') and settings.get('reduce_fps'):
            issues.append("VMAF needs matching frame rates; halving FPS while deinterlacing will make it fail.")

        return issues
