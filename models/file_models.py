"""
File models for videer
Handles file objects and their properties
"""

import os
import logging
from typing import Optional, List, Dict, Any


class VideoFile:
    """Represents a video file to be processed"""
    
    def __init__(self, filepath: str, index: int = 0):
        self.index = index
        self.filepath = filepath
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
        
        # Processing state
        self.is_processing = False
        self.is_completed = False
        self.has_error = False
        self.error_messages: List[str] = []
        
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
    
    def create_logger(self):
        """Create a logger for this file"""
        log_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)-5.5s] %(message)s"
        )
        
        self.logger = logging.getLogger(f"file_{self.index}_{self.basename}")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()
        
        # File handler
        log_file = os.path.join(self.directory, f"{self.basename}.log")
        file_handler = logging.FileHandler(log_file)
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
        
        self.output_name = f"{self.basename}{codec_suffix}{quality_suffix}.{output_format}"
        
        # Set transcode name if needed
        if settings.get('transcode_video') or settings.get('transcode_audio'):
            self.transcode_name = f"{self.basename}.trans.avi"
        
        # Set AviSynth file if needed
        if settings.get('use_avisynth'):
            self.avs_file = f"{self.basename}.avs"
        
        # Set index files
        self.ffindex_file = f"{self.filepath}.ffindex"
        self.error_file = f"{self.filepath}.error"
    
    def get_full_output_path(self, output_dir: Optional[str] = None) -> str:
        """Get full path for output file"""
        if output_dir:
            return os.path.join(output_dir, self.output_name)
        return os.path.join(self.directory, self.output_name)
    
    def get_file_size_mb(self) -> float:
        """Get file size in MB"""
        try:
            return os.path.getsize(self.filepath) / (1024 * 1024)
        except:
            return 0.0
    
    def add_error(self, message: str):
        """Add an error message"""
        self.error_messages.append(message)
        self.has_error = True
        if self.logger:
            self.logger.error(message)
    
    def log_info(self, message: str):
        """Log an info message"""
        if self.logger:
            self.logger.info(message)
    
    def cleanup_temp_files(self):
        """Remove temporary files created during processing"""
        temp_files = [
            self.transcode_name,
            self.avs_file,
            self.ffindex_file,
            self.error_file
        ]
        
        for file in temp_files:
            if file and os.path.exists(file):
                try:
                    os.remove(file)
                    self.log_info(f"Removed temp file: {file}")
                except Exception as e:
                    self.log_info(f"Failed to remove temp file {file}: {e}")
    
    def __repr__(self) -> str:
        return f"<VideoFile: {self.filename}>"


class FileQueue:
    """Manages a queue of video files"""
    
    def __init__(self):
        self.files: List[VideoFile] = []
        self._current_index = 0
    
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
        self._current_index = 0
    
    def contains(self, filepath: str) -> bool:
        """Check if filepath is already in queue"""
        return any(f.filepath == filepath for f in self.files)
    
    def get_all(self) -> List[VideoFile]:
        """Get all files in queue"""
        return self.files.copy()
    
    def get_next(self) -> Optional[VideoFile]:
        """Get next file to process"""
        if self._current_index < len(self.files):
            file = self.files[self._current_index]
            self._current_index += 1
            return file
        return None
    
    def reset_iterator(self):
        """Reset the iterator to beginning"""
        self._current_index = 0
    
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