"""
Process Manager for videer
Handles FFmpeg process execution and monitoring
"""

import os
import time
import subprocess
import re
import psutil
import shlex
from typing import List, Dict, Any, Optional
from PySide6.QtCore import QThread, Signal, QObject

from models.file_models import VideoFile
from utils.ffmpeg_utils import FFmpegCommandBuilder, find_ffmpeg
from modules.avisynth_handler import AviSynthHandler
from utils.file_utils import FileOperations


class ProcessThread(QThread):
    """Thread for processing video files"""

    # Signals
    progress_signal = Signal(str, int)  # message, percentage
    info_signal = Signal(str)
    file_started = Signal(int)  # file index
    file_finished = Signal(int, bool)  # file index, success
    time_remaining = Signal(str)  # time string
    processing_finished = Signal(int, int)  # success count, total count

    def __init__(self, files: List[VideoFile], settings: Dict[str, Any]):
        super().__init__()
        self.files = files
        self.settings = settings
        self.should_stop = False
        self.current_process = None
        self.current_pid = None
        self.start_time = None
        self.success_count = 0

        self.command_builder = FFmpegCommandBuilder(settings)
        self.avisynth_handler = AviSynthHandler(settings) if settings.get('use_avisynth') else None
        self.file_ops = FileOperations()

    def run(self):
        """Main processing loop"""
        self.start_time = time.time()
        self.success_count = 0
        total_count = len(self.files)

        for i, file in enumerate(self.files):
            if self.should_stop:
                break

            # Setup file for processing
            file.create_logger()
            file.set_output_name(self.settings)

            self.file_started.emit(i)
            self.info_signal.emit(f"Processing {i + 1}/{total_count}: {file.filename}")

            # Calculate time remaining
            if i > 0:
                self._emit_time_remaining(i, total_count)

            # Process file
            success = self._process_file(file)

            if success:
                self.success_count += 1

                # Handle file replacement if requested
                if self.settings.get('replace_files'):
                    self.file_ops.replace_file(
                        file.get_full_output_path(),
                        file.filepath,
                        file.logger
                    )
                else:
                    # Preserve timestamps
                    self.file_ops.preserve_timestamps(
                        file.filepath,
                        file.get_full_output_path(),
                        file.logger
                    )

            # Cleanup temporary files
            file.cleanup_temp_files()

            # Emit completion
            self.file_finished.emit(i, success)

            # Report errors if any
            if file.error_messages:
                self.info_signal.emit(f"Errors in {file.filename}:\n" + '\n'.join(file.error_messages))

        self.processing_finished.emit(self.success_count, total_count)

    def _process_file(self, file: VideoFile) -> bool:
        """Process a single file"""
        try:
            # Transcode if needed
            if self.settings.get('transcode_video') or self.settings.get('transcode_audio'):
                if not self._transcode(file):
                    return False
                # Update filename to transcoded version
                input_file = file.transcode_name
            else:
                input_file = file.filepath

            # Create AviSynth script if needed
            if self.settings.get('use_avisynth') and self.avisynth_handler:
                if self.avisynth_handler.create_script(file):
                    input_file = file.avs_file
                else:
                    file.add_error("Failed to create AviSynth script")
                    return False

            # Build and execute main command
            command = self.command_builder.build_main_command(
                input_file,
                file.get_full_output_path(),
                self.settings.get('use_avisynth', False)
            )

            return_code = self._execute_command(command, file)

            return return_code == 0 and not self.should_stop

        except Exception as e:
            file.add_error(f"Processing error: {str(e)}")
            return False

    def _transcode(self, file: VideoFile) -> bool:
        """Transcode to raw format"""
        file.log_info("Starting transcoding...")

        command = self.command_builder.build_transcode_command(
            file.filepath,
            file.transcode_name,
            self.settings.get('transcode_video', False),
            self.settings.get('transcode_audio', False)
        )

        return_code = self._execute_command(command, file)
        success = return_code == 0 and not self.should_stop

        if success:
            file.log_info("Transcoding completed successfully")
        else:
            file.add_error("Transcoding failed")

        return success

    def _execute_command(self, command: List[str], file: VideoFile) -> int:
        """Execute FFmpeg command and monitor progress"""
        # Properly quote each argument to handle spaces in paths
        quoted_command = []
        for arg in command:
            # Don't quote arguments that are already quoted or are flags
            if ' ' in arg and not arg.startswith('"') and not arg.startswith("'") and not arg.startswith('-'):
                quoted_command.append(f'"{arg}"')
            else:
                quoted_command.append(arg)

        command_str = ' '.join(quoted_command)
        file.log_info(f"Executing: {command_str}")

        try:
            # Create process with proper shell handling for Windows
            if os.name == 'nt':  # Windows
                self.current_process = subprocess.Popen(
                    command_str,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    encoding='utf-8',
                    shell=True,
                    bufsize=1
                )
            else:  # Unix-like systems
                # Use shlex to properly split the command for Unix
                self.current_process = subprocess.Popen(
                    shlex.split(command_str),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    encoding='utf-8',
                    shell=False,
                    bufsize=1
                )

            self.current_pid = self.current_process.pid

            # Process output
            duration_pattern = re.compile(r'Duration: (\d{2}):(\d{2}):(\d{2})')
            time_pattern = re.compile(r'time=(\d{2}):(\d{2}):(\d{2})')
            error_keywords = ["error", "invalid", "failed"]

            total_duration = None

            for line in self.current_process.stdout:
                if self.should_stop:
                    self._kill_process()
                    return 1

                # Log line
                line = line.strip()
                if line:
                    file.log_info(line)

                # Check for duration
                if not total_duration:
                    duration_match = duration_pattern.search(line)
                    if duration_match:
                        h, m, s = map(int, duration_match.groups())
                        total_duration = h * 3600 + m * 60 + s

                # Check for progress
                time_match = time_pattern.search(line)
                if time_match and total_duration:
                    h, m, s = map(int, time_match.groups())
                    current_time = h * 3600 + m * 60 + s
                    progress = int((current_time / total_duration) * 100)

                    # Parse and clean the status line
                    clean_line = self._clean_status_line(line)
                    self.progress_signal.emit(clean_line, progress)

                # Check for errors
                line_lower = line.lower()
                for error_keyword in error_keywords:
                    if error_keyword in line_lower:
                        file.add_error(line)
                        break

            # Wait for process to complete
            return_code = self.current_process.wait()
            self.current_process = None
            self.current_pid = None

            file.log_info(f"Process completed with return code: {return_code}")
            return return_code

        except Exception as e:
            file.add_error(f"Command execution error: {str(e)}")
            return 1

    def _clean_status_line(self, line: str) -> str:
        """Clean FFmpeg status line for display"""
        replacements = {
            "       ": " ",
            "    ": " ",
            "time=": "",
            "bitrate=  ": "br:",
            "speed": "rate",
            "size=": "",
            "frame": "f",
            "=": ":",
            "\n": ""
        }

        for old, new in replacements.items():
            line = line.replace(old, new)

        return line.strip()

    def _emit_time_remaining(self, current_index: int, total: int):
        """Calculate and emit estimated time remaining"""
        elapsed = time.time() - self.start_time
        avg_time_per_file = elapsed / current_index
        remaining_files = total - current_index
        est_remaining = avg_time_per_file * remaining_files

        hours = int(est_remaining // 3600)
        minutes = int((est_remaining % 3600) // 60)
        seconds = int(est_remaining % 60)

        if hours > 0:
            time_str = f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            time_str = f"{minutes}m {seconds}s"
        else:
            time_str = f"{seconds}s"

        self.time_remaining.emit(f"Est. remaining: {time_str}")

    def stop(self):
        """Stop processing"""
        self.should_stop = True
        self._kill_process()

    def _kill_process(self):
        """Kill current FFmpeg process and children"""
        if self.current_pid:
            try:
                parent = psutil.Process(self.current_pid)
                children = parent.children(recursive=True)

                # Kill children first
                for child in children:
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass

                # Kill parent
                try:
                    parent.kill()
                except psutil.NoSuchProcess:
                    pass

                self.current_pid = None
                self.current_process = None

            except psutil.NoSuchProcess:
                pass


class ProcessManager(QObject):
    """Manages video processing operations"""

    # Signals
    progress_updated = Signal(int, int)  # current, total
    status_updated = Signal(str)
    processing_finished = Signal(int, int)  # success count, total count

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.process_thread: Optional[ProcessThread] = None
        self._is_processing = False

    def start_processing(self, files: List[VideoFile], settings: Dict[str, Any]):
        """Start processing files with given settings"""
        if self._is_processing:
            return

        # Check FFmpeg availability
        if not find_ffmpeg():
            self.status_updated.emit("Error: FFmpeg not found!")
            return

        # Files are already VideoFile objects, use them directly
        video_files = files
        
        # Create and start process thread
        self.process_thread = ProcessThread(video_files, settings)
        
        # Connect signals
        self.process_thread.progress_signal.connect(self._on_progress)
        self.process_thread.info_signal.connect(self._on_info)
        self.process_thread.file_started.connect(self._on_file_started)
        self.process_thread.file_finished.connect(self._on_file_finished)
        self.process_thread.time_remaining.connect(self._on_time_remaining)
        self.process_thread.processing_finished.connect(self._on_processing_finished)
        
        # Start processing
        self._is_processing = True
        self.process_thread.start()
        
        self.progress_updated.emit(0, len(files))
        self.status_updated.emit("Processing started...")
    
    def stop_processing(self):
        """Stop current processing"""
        if self.process_thread and self.process_thread.isRunning():
            self.process_thread.stop()
            self.process_thread.wait(5000)  # Wait up to 5 seconds
            
            if self.process_thread.isRunning():
                self.process_thread.terminate()  # Force terminate if still running
            
            self._is_processing = False
            self.status_updated.emit("Processing stopped")
    
    def is_processing(self) -> bool:
        """Check if currently processing"""
        return self._is_processing
    
    def _on_progress(self, message: str, percentage: int):
        """Handle progress update"""
        self.status_updated.emit(message)
    
    def _on_info(self, message: str):
        """Handle info message"""
        print(f"[INFO] {message}")  # Could add to a log viewer
    
    def _on_file_started(self, index: int):
        """Handle file processing start"""
        # Update UI to show file is being processed
        if hasattr(self.main_window, 'ui_manager'):
            if hasattr(self.main_window.ui_manager, 'file_list'):
                item = self.main_window.ui_manager.file_list.item(index)
                if item:
                    from PySide6.QtCore import Qt
                    item.setBackground(Qt.GlobalColor.yellow)
    
    def _on_file_finished(self, index: int, success: bool):
        """Handle file processing completion"""
        # Update UI to show file completion status
        if hasattr(self.main_window, 'ui_manager'):
            if hasattr(self.main_window.ui_manager, 'file_list'):
                item = self.main_window.ui_manager.file_list.item(index)
                if item:
                    from PySide6.QtCore import Qt
                    color = Qt.GlobalColor.green if success else Qt.GlobalColor.red
                    item.setBackground(color)
        
        # Update progress bar
        if self.process_thread:
            total = len(self.process_thread.files)
            current = index + 1
            self.progress_updated.emit(current, total)
    
    def _on_time_remaining(self, time_str: str):
        """Handle time remaining update"""
        if hasattr(self.main_window, 'ui_manager'):
            if hasattr(self.main_window.ui_manager, 'time_label'):
                self.main_window.ui_manager.time_label.setText(time_str)
    
    def _on_processing_finished(self, success_count: int, total_count: int):
        """Handle processing completion"""
        self._is_processing = False
        self.status_updated.emit(f"Completed: {success_count}/{total_count} files processed successfully")
        self.processing_finished.emit(success_count, total_count)
    
    def validate_settings(self, settings: Dict[str, Any]) -> List[str]:
        """
        Validate processing settings
        Returns list of warnings/errors
        """
        issues = []
        
        # Check codec compatibility
        video_codec = settings.get('video_codec')
        output_format = settings.get('output_format', '').lower()
        
        if video_codec == 'prores_ks' and output_format not in ['mov', 'mkv']:
            issues.append("ProRes codec works best with MOV or MKV containers")
        
        if output_format == 'webm' and video_codec not in ['libvpx', 'libvpx-vp9']:
            issues.append("WebM requires VP8/VP9 video codec")
        
        # Check AviSynth requirements
        if settings.get('use_avisynth'):
            if settings.get('deinterlace') and not settings.get('use_ffms2'):
                issues.append("Deinterlacing requires FFMS2 source filter")
        
        return issues