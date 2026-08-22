"""
File models for videer
Handles file objects and their properties
"""

import os
import logging
import logging.handlers
from collections import deque
from typing import Optional, List, Dict, Any

# A damaged source encoded with -err_detect can make FFmpeg emit an error line per packet. Keeping all of them
# costs gigabytes of RAM per hour and drives the machine into swap, so keep a head (what went wrong first) and
# a rolling tail (how it ended) and count the rest.
MAX_ERRORS_HEAD = 200
MAX_ERRORS_TAIL = 200

# Same spew reaches the per-file log. Rotate so one bad input cannot fill the media drive overnight.
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 2


class BoundedFileHandler(logging.handlers.RotatingFileHandler):
    """
    RotatingFileHandler that checks its size every CHECK_EVERY records instead of on every one.

    The stock handler calls stream.tell() (and formats the record a second time) for each line written, which
    roughly halves throughput on the exact workload this cap exists for — an encode logging an error per
    packet. Amortising the check costs at most CHECK_EVERY lines of overshoot past maxBytes.
    """

    CHECK_EVERY = 256

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._since_check = 0

    def shouldRollover(self, record):
        self._since_check += 1
        if self._since_check < self.CHECK_EVERY:
            return False
        self._since_check = 0
        return super().shouldRollover(record)


def canonical_path(filepath: str) -> str:
    """
    Identity key for duplicate detection: absolute, symlink-resolved,
    case-normalized (case-insensitive filesystems on Windows/macOS).
    """
    try:
        resolved = os.path.realpath(os.path.abspath(filepath))
    except OSError:
        resolved = os.path.abspath(filepath)
    return os.path.normcase(resolved)


class VideoFile:
    """Represents a video file to be processed"""
    
    def __init__(self, filepath: str, index: int = 0):
        self.index = index
        self.filepath = filepath
        self.canonical = canonical_path(filepath)
        self.filename = os.path.basename(filepath)
        self.directory = os.path.dirname(os.path.abspath(filepath))
        self.basename = os.path.splitext(self.filename)[0]
        self.extension = os.path.splitext(self.filename)[1]
        
        # Output names
        self.output_name: Optional[str] = None
        self.transcode_name: Optional[str] = None
        self.avs_file: Optional[str] = None
        self.error_file: Optional[str] = None
        self.ffindex_file: Optional[str] = None
        
        # Processing state: 'pending' | 'running' | 'success' | 'failed'
        self.status = 'pending'
        self.has_error = False
        self.error_messages: List[str] = []          # bounded head; see add_error / get_error_report
        self._error_tail: deque = deque(maxlen=MAX_ERRORS_TAIL)
        self.error_count = 0                         # total seen, including the ones not retained
        
        # Logger
        self.logger: Optional[logging.Logger] = None
        
        # Video properties
        self.duration: Optional[float] = None
        self.width: Optional[int] = None
        self.height: Optional[int] = None
        self.fps: Optional[float] = None
        self.bitrate: Optional[int] = None
        self.codec: Optional[str] = None
        
        # PAR/DAR properties
        self.sample_aspect_ratio: Optional[str] = None
        self.display_aspect_ratio: Optional[str] = None
        self.pixel_aspect_ratio: Optional[str] = None

        # Quality metrics
        self.vmaf_score: Optional[float] = None
    
    def create_logger(self):
        """Create a rotating logger for this file"""
        log_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)-5.5s] %(message)s"
        )

        # Instantiated directly rather than via getLogger(): the logging manager keeps every name it is asked
        # for alive for the lifetime of the process, so a getLogger() per file leaks one logger per file.
        self.close_logger()             # close, don't just drop: handlers.clear() leaks the open descriptor
        self.logger = logging.Logger(f"videer.file.{self.index}", logging.INFO)
        self.logger.propagate = False   # keep per-file logs out of the root logger / stderr

        # Rotating file handler: an encode that logs an error per packet cannot fill the drive
        log_file = os.path.join(self.directory, f"{self.basename}.log")
        file_handler = BoundedFileHandler(
            log_file, mode='w', maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding='utf-8')
        file_handler.setFormatter(log_formatter)
        self.logger.addHandler(file_handler)

        return self.logger
    
    def set_output_name(self, settings: Dict[str, Any]):
        """Generate output filename based on settings"""
        output_format = settings.get('output_format', 'mkv').lower()
        video_codec = settings.get('video_codec', 'libx265')
        audio_codec = settings.get('audio_codec', 'aac')
        crf = settings.get('crf', 23)
        abr = settings.get('abr', 256)
        
        # Build output filename
        codec_suffix = f"_{video_codec}_{audio_codec}"
        quality_suffix = f"_crf{crf}_abr{abr}"
        
        resolution_suffix = self._resolution_suffix(settings)

        self.output_name = (f"{self.basename}{codec_suffix}{quality_suffix}"
                            f"{resolution_suffix}.{output_format}")
        
        # Intermediates live next to the source, never in the process CWD: a relative name sends a raw AVI
        # (~100 GB per hour of 1080p) into the install directory and makes two same-named sources collide.
        if settings.get('transcode_video') or settings.get('transcode_audio'):
            self.transcode_name = os.path.join(self.directory, f"{self.basename}.trans.avi")

        # Set AviSynth file if needed
        if settings.get('use_avisynth'):
            self.avs_file = os.path.join(self.directory, f"{self.basename}.avs")
        
        # Set index files
        self.ffindex_file = f"{self.filepath}.ffindex"
        self.error_file = f"{self.filepath}.error"
    
    @staticmethod
    def _resolution_suffix(settings: Dict[str, Any]) -> str:
        """Filename suffix describing the requested output resolution, if any"""
        mode = settings.get('resolution_mode') or ''
        if not mode or mode.startswith('Original'):
            return ''
        if mode == 'Custom':
            width = int(settings.get('custom_width') or 0)
            height = int(settings.get('custom_height') or 0)
            if width <= 0 and height <= 0:
                return ''
            return f"_{width if width > 0 else 'auto'}x{height if height > 0 else 'auto'}"
        # e.g. "1080p (Full HD)" -> "1080p"
        return f"_{mode.split(' ')[0]}"

    def get_full_output_path(self, output_dir: Optional[str] = None) -> str:
        """Final output path — it only ever exists once the encode has completed"""
        if output_dir:
            return os.path.join(output_dir, self.output_name)
        return os.path.join(self.directory, self.output_name)

    def get_temp_output_path(self, output_dir: Optional[str] = None) -> str:
        """Where FFmpeg writes while encoding: <name>.part.<ext> (same extension, so the container is inferred)"""
        stem, ext = os.path.splitext(self.get_full_output_path(output_dir))
        return f"{stem}.part{ext}"
    
    def get_file_size_mb(self) -> float:
        """Get file size in MB"""
        try:
            return os.path.getsize(self.filepath) / (1024 * 1024)
        except:
            return 0.0
    
    def add_error(self, message: str):
        """
        Record an error line. Retention is bounded: the first MAX_ERRORS_HEAD lines and the last
        MAX_ERRORS_TAIL are kept, everything between them is counted and dropped. A damaged source can
        otherwise produce millions of these and take the machine into swap.
        """
        self.has_error = True
        self.error_count += 1
        if len(self.error_messages) < MAX_ERRORS_HEAD:
            self.error_messages.append(message)
        else:
            self._error_tail.append(message)
        if self.logger:
            self.logger.error(message)

    def get_error_report(self) -> str:
        """Human-readable error summary: head, an elision marker, then the tail"""
        if not self.error_count:
            return ""
        parts = list(self.error_messages)
        dropped = self.error_count - len(self.error_messages) - len(self._error_tail)
        if dropped > 0:
            parts.append(f"... {dropped} further error lines omitted ...")
        parts.extend(self._error_tail)
        return '\n'.join(parts)

    def clear_errors(self):
        """Release retained error text (called once a file is done with)"""
        self.error_messages = []
        self._error_tail.clear()
    
    def log_info(self, message: str):
        """Log an info message"""
        if self.logger:
            self.logger.info(message)
    
    def cleanup_temp_files(self):
        """Remove temporary files created during processing"""
        temp_files = []
        # output_name is unset when the file failed before set_output_name() ran; there is no .part to remove
        # and building its path would raise, taking the rest of the cleanup with it.
        if self.output_name:
            temp_files.append(self.get_temp_output_path())
        temp_files.extend([
            self.transcode_name,
            self.avs_file,
            self.ffindex_file,
            self.error_file
        ])


        for file in temp_files:
            if file and os.path.exists(file):
                try:
                    os.remove(file)
                    self.log_info(f"Removed temp file: {file}")
                except Exception as e:
                    self.log_info(f"Failed to remove temp file {file}: {e}")

        self.close_logger()

    def close_logger(self):
        """Flush and close log handlers so the log file handle is released"""
        if self.logger:
            for handler in list(self.logger.handlers):
                handler.close()
                self.logger.removeHandler(handler)
    
    def __repr__(self) -> str:
        return f"<VideoFile: {self.filename}>"


class FileQueue:
    """Manages a queue of video files"""
    
    def __init__(self):
        self.files: List[VideoFile] = []
    
    def add_file(self, filepath: str) -> VideoFile:
        """Add a file to the queue"""
        if not self.contains(filepath):
            video_file = VideoFile(filepath, len(self.files))
            self.files.append(video_file)
            return video_file
        return None
    
    def add_files(self, filepaths: List[str]) -> List[VideoFile]:
        """Add multiple files to the queue"""
        added = []
        for filepath in filepaths:
            file = self.add_file(filepath)
            if file:
                added.append(file)
        return added
    
    def remove_file(self, filepath: str) -> bool:
        """Remove a file from the queue"""
        for i, file in enumerate(self.files):
            if file.filepath == filepath:
                del self.files[i]
                self._reindex()
                return True
        return False
    
    def remove_at_index(self, index: int) -> bool:
        """Remove file at specific index"""
        if 0 <= index < len(self.files):
            del self.files[index]
            self._reindex()
            return True
        return False
    
    def clear(self):
        """Clear all files from queue"""
        self.files.clear()
    
    def contains(self, filepath: str) -> bool:
        """Check if filepath (or another spelling of the same file) is already in queue"""
        key = canonical_path(filepath)
        return any(f.canonical == key for f in self.files)
    
    def get_all(self) -> List[VideoFile]:
        """Get all files in queue"""
        return self.files.copy()
    
    def _reindex(self):
        """Reindex files after removal"""
        for i, file in enumerate(self.files):
            file.index = i
    
    def __len__(self) -> int:
        return len(self.files)
    
    def __bool__(self) -> bool:
        return bool(self.files)
    
    def __iter__(self):
        return iter(self.files)
    
    def __getitem__(self, index: int) -> VideoFile:
        return self.files[index]