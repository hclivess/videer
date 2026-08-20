"""
File Manager module for videer
Handles file queue management and file operations
"""

import os
import re
from typing import List, Optional
from PySide6.QtCore import QObject, Signal

from models.file_models import VideoFile, FileQueue
from config import VIDEO_EXTENSIONS


def _natural_sort_key(name: str):
    """Sort key that orders embedded numbers numerically (1, 2, 10, 20)"""
    return [int(part) if part.isdigit() else part.casefold()
            for part in re.split(r'(\d+)', name)]


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
            # Dropped folders: expand recursively to the video files they contain
            if os.path.isdir(filepath):
                for root, dirs, entries in os.walk(filepath):
                    dirs.sort(key=_natural_sort_key)
                    for entry in sorted(entries, key=_natural_sort_key):
                        candidate = os.path.join(root, entry)
                        if self._is_valid_video_file(candidate) and not self.queue.contains(candidate):
                            valid_files.append(candidate)
                            added_count += 1
                continue
            if self._is_valid_video_file(filepath):
                if not self.queue.contains(filepath):
                    valid_files.append(filepath)
                    added_count += 1
        
        if valid_files:
            # Multi-selects arrive in OS selection order (often lexicographic:
            # 1, 10, 2, 20) — order each added batch naturally instead
            if len(valid_files) > 1:
                valid_files.sort(key=_natural_sort_key)
            self.queue.add_files(valid_files)
            self._emit_updates()
        
        return added_count
    
    def add_folder(self, folder_path: str) -> int:
        """
        Add all video files from a folder (recursive, natural order)
        Returns number of files added
        """
        if not os.path.isdir(folder_path):
            return 0
        return self.add_files([folder_path])
    
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
        """Move a file within the queue (list drag-and-drop reordering)"""
        files = self.queue.files
        if not (0 <= from_index < len(files) and 0 <= to_index < len(files)) or from_index == to_index:
            return False
        files.insert(to_index, files.pop(from_index))
        self.queue._reindex()
        self._emit_updates()
        return True

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
