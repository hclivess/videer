"""
File Manager module for videer
Handles file queue management and file operations
"""

import os
from typing import List, Optional
from PySide6.QtCore import QObject, Signal

from models.file_models import VideoFile, FileQueue, canonical_path
from config import VIDEO_EXTENSIONS
from utils.naturalsort import natural_key, path_key


class FileManager(QObject):
    """Manages the file queue and file operations"""
    
    # Signals
    files_updated = Signal(list)  # List of VideoFile objects
    file_count_changed = Signal(int)  # Number of files in queue
    duplicates_skipped = Signal(list)  # Paths that were already queued (or repeated in the drop)
    
    def __init__(self):
        super().__init__()
        self.queue = FileQueue()
    
    def add_files(self, filepaths: List[str], preserve_order: bool = False) -> int:
        """
        Add files to the queue, skipping duplicates.
        A file counts as a duplicate if it is already queued *or* appears twice
        in the same batch — compared by resolved, case-normalized path, so
        `C:\\Video\\a.mkv`, `c:/video/A.MKV` and a symlink to it are all one file.
        With preserve_order the batch keeps the order it was given (a queue restored
        from a saved file must come back in the order the user arranged it).
        Returns number of files added.
        """
        valid_files = []
        skipped = []
        seen = set()

        def consider(candidate: str):
            if not self._is_valid_video_file(candidate):
                return
            key = canonical_path(candidate)
            if key in seen or self.queue.contains(candidate):
                skipped.append(candidate)
                return
            seen.add(key)
            valid_files.append(candidate)

        for filepath in filepaths:
            # Dropped folders: expand recursively to the video files they contain
            if os.path.isdir(filepath):
                for root, dirs, entries in os.walk(filepath):
                    dirs.sort(key=natural_key)
                    for entry in sorted(entries, key=natural_key):
                        consider(os.path.join(root, entry))
                continue
            consider(filepath)

        if valid_files:
            # Multi-selects arrive in OS selection order (often lexicographic:
            # 1, 10, 2, 20) — order each added batch naturally instead
            if len(valid_files) > 1 and not preserve_order:
                valid_files.sort(key=path_key)
            self.queue.add_files(valid_files)
            self._emit_updates()

        if skipped:
            self.duplicates_skipped.emit(skipped)

        return len(valid_files)
    
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
    
    def refresh(self):
        """Re-emit the queue (used after mutating files in place, e.g. restoring saved statuses)"""
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
    
    def reorder(self, canonical_order: List[str]) -> bool:
        """
        Put the queue into the given order, identified by canonical path.

        Driven by the order the file list ends up in after a drag. Any file the caller does not mention
        keeps its relative position at the end, so an order that is stale or incomplete can only ever be a
        partial reordering — never a way to lose an entry.
        """
        files = self.queue.files
        by_key: dict = {}
        for file in files:
            by_key.setdefault(file.canonical, []).append(file)

        ordered = []
        for key in canonical_order:
            bucket = by_key.get(key)
            if bucket:
                ordered.append(bucket.pop(0))

        taken = {id(file) for file in ordered}
        ordered.extend(file for file in files if id(file) not in taken)

        if ordered == files:
            return False
        files[:] = ordered
        self.queue._reindex()
        self._emit_updates()
        return True

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
