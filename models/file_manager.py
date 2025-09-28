"""
File Manager module for videer
Handles file queue management and file operations
"""

import os
from typing import List, Optional
from PySide6.QtCore import QObject, Signal

from models.file_models import VideoFile, FileQueue
from config import VIDEO_EXTENSIONS


class FileManager(QObject):
    """Manages the file queue and file operations"""
    
    # Signals
    files_updated = Signal(list)  # List of VideoFile objects
    file_count_changed = Signal(int)  # Number of files in queue
    
    def __init__(self):
        super().__init__()
        self.queue = FileQueue()
    
    def add_files(self, filepaths: List[str]) -> int:
        """
        Add files to the queue
        Returns number of files added
        """
        added_count = 0
        valid_files = []
        
        for filepath in filepaths:
            if self._is_valid_video_file(filepath):
                if not self.queue.contains(filepath):
                    valid_files.append(filepath)
                    added_count += 1
        
        if valid_files:
            self.queue.add_files(valid_files)
            self._emit_updates()
        
        return added_count
    
    def add_folder(self, folder_path: str, recursive: bool = False) -> int:
        """
        Add all video files from a folder
        Returns number of files added
        """
        if not os.path.isdir(folder_path):
            return 0
        
        files_to_add = []
        
        if recursive:
            # Walk through all subdirectories
            for root, dirs, files in os.walk(folder_path):
                for file in files:
                    filepath = os.path.join(root, file)
                    if self._is_valid_video_file(filepath):
                        files_to_add.append(filepath)
        else:
            # Only current directory
            for file in os.listdir(folder_path):
                filepath = os.path.join(folder_path, file)
                if os.path.isfile(filepath) and self._is_valid_video_file(filepath):
                    files_to_add.append(filepath)
        
        return self.add_files(files_to_add)
    
    def remove_files(self, indices: List[int]) -> int:
        """
        Remove files at specified indices
        Returns number of files removed
        """
        removed_count = 0
        
        # Sort indices in reverse order to avoid index shifting
        for index in sorted(indices, reverse=True):
            if self.queue.remove_at_index(index):
                removed_count += 1
        
        if removed_count > 0:
            self._emit_updates()
        
        return removed_count
    
    def remove_file_by_path(self, filepath: str) -> bool:
        """Remove a specific file by its path"""
        if self.queue.remove_file(filepath):
            self._emit_updates()
            return True
        return False
    
    def clear_queue(self):
        """Clear all files from the queue"""
        self.queue.clear()
        self._emit_updates()
    
    def get_queue(self) -> List[VideoFile]:
        """Get all files in the queue"""
        return self.queue.get_all()
    
    def get_file(self, index: int) -> Optional[VideoFile]:
        """Get file at specific index"""
        if 0 <= index < len(self.queue):
            return self.queue[index]
        return None
    
    def get_file_count(self) -> int:
        """Get number of files in queue"""
        return len(self.queue)
    
    def has_files(self) -> bool:
        """Check if queue has any files"""
        return bool(self.queue)
    
    def move_file(self, from_index: int, to_index: int) -> bool:
        """
        Move file from one position to another in queue
        Used for drag and drop reordering
        """
        if (0 <= from_index < len(self.queue) and 
            0 <= to_index < len(self.queue) and 
            from_index != to_index):
            
            files = self.queue.files
            file_to_move = files.pop(from_index)
            files.insert(to_index, file_to_move)
            self.queue._reindex()
            self._emit_updates()
            return True
        
        return False
    
    def get_total_size_mb(self) -> float:
        """Get total size of all files in queue (MB)"""
        total_size = 0
        for file in self.queue:
            total_size += file.get_file_size_mb()
        return total_size
    
    def _is_valid_video_file(self, filepath: str) -> bool:
        """Check if file is a valid video file"""
        if not os.path.isfile(filepath):
            return False
        
        ext = os.path.splitext(filepath)[1].lower()
        return ext in VIDEO_EXTENSIONS
    
    def _emit_updates(self):
        """Emit signals to notify about queue changes"""
        self.files_updated.emit(self.queue.get_all())
        self.file_count_changed.emit(len(self.queue))
    
    def validate_queue(self) -> List[str]:
        """
        Validate all files in queue still exist
        Returns list of missing files
        """
        missing = []
        for file in self.queue:
            if not os.path.exists(file.filepath):
                missing.append(file.filepath)
        
        # Remove missing files
        for filepath in missing:
            self.queue.remove_file(filepath)
        
        if missing:
            self._emit_updates()
        
        return missing
    
    def get_output_paths(self, settings: dict, output_dir: Optional[str] = None) -> List[str]:
        """
        Get list of output paths based on current settings
        Useful for checking if files will be overwritten
        """
        output_paths = []
        
        for file in self.queue:
            file.set_output_name(settings)
            output_path = file.get_full_output_path(output_dir)
            output_paths.append(output_path)
        
        return output_paths
    
    def check_overwrites(self, settings: dict, output_dir: Optional[str] = None) -> List[str]:
        """
        Check which output files already exist
        Returns list of existing files that would be overwritten
        """
        existing = []
        output_paths = self.get_output_paths(settings, output_dir)
        
        for path in output_paths:
            if os.path.exists(path):
                existing.append(path)
        
        return existing