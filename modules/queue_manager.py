"""
Queue Manager for videer
Saves the file queue to disk so it survives a restart and can be re-imported later.

Two paths share the same on-disk format:
  * explicit Save Queue… / Load Queue… (a file the user picks and keeps)
  * an autosave next to the application, rewritten whenever the queue changes,
    restored silently on the next start — this is what survives a crash mid-batch.
"""

import os
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
                               QLabel, QMessageBox, QPushButton, QVBoxLayout)

from config import APP_NAME, APP_VERSION, QUEUE_AUTOSAVE_FILE, QUEUE_FILE_FORMAT
from models.file_models import canonical_path

QUEUE_FILE_FILTER = "videer Queue (*.json);;All Files (*.*)"

# Statuses that mean "this file still needs encoding". 'running' counts as unfinished: a queue saved while a
# file was in flight was interrupted, and its output was never renamed off .part.
UNFINISHED = ('pending', 'running', 'failed')


def serialize_queue(files: List[Any], settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build the on-disk representation of a queue"""
    return {
        "app": APP_NAME,
        "format": QUEUE_FILE_FORMAT,
        "version": APP_VERSION,
        "saved_at": datetime.now().isoformat(timespec='seconds'),
        "settings": settings,
        "files": [{"path": os.path.abspath(f.filepath),
                   "status": getattr(f, 'status', 'pending')} for f in files],
    }


def write_queue_file(path: str, data: Dict[str, Any]):
    """
    Write the queue JSON atomically: the autosave is rewritten on every queue change, and a process killed
    mid-write would otherwise leave a truncated file that cannot be restored at all.
    """
    tmp = f"{path}.tmp"
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, indent=4)
    os.replace(tmp, path)


def read_queue_file(path: str) -> Dict[str, Any]:
    """Read and validate a queue file. Raises ValueError if it is not one."""
    with open(path, 'r', encoding='utf-8') as handle:
        data = json.load(handle)

    if not isinstance(data, dict) or not isinstance(data.get('files'), list):
        raise ValueError("not a videer queue file")

    entries = []
    for entry in data['files']:
        # Tolerate a bare list of paths — hand-written queue files are a reasonable thing to feed this
        if isinstance(entry, str):
            entries.append({"path": entry, "status": "pending"})
        elif isinstance(entry, dict) and isinstance(entry.get('path'), str):
            entries.append({"path": entry['path'],
                            "status": entry.get('status') if entry.get('status') in
                            ('pending', 'running', 'success', 'failed') else 'pending'})
    data['files'] = entries
    if not isinstance(data.get('settings'), dict):
        data['settings'] = None
    return data


class QueueLoadDialog(QDialog):
    """What to do with a queue file that is about to be loaded"""

    def __init__(self, parent, name: str, entries: List[Dict[str, Any]],
                 missing: int, has_settings: bool, queue_not_empty: bool):
        super().__init__(parent)
        self.setWindowTitle("Load Queue")
        self.mode: Optional[str] = None

        completed = sum(1 for e in entries if e['status'] == 'success')
        layout = QVBoxLayout(self)

        summary = f"{name}\n\n{len(entries)} file(s) in this queue."
        if completed:
            summary += f" {completed} marked completed."
        if missing:
            summary += f" {missing} no longer exist(s) on disk and will be skipped."
        layout.addWidget(QLabel(summary))

        self.skip_completed = None
        if completed:
            self.skip_completed = QCheckBox(f"Skip the {completed} file(s) already completed")
            self.skip_completed.setChecked(True)
            layout.addWidget(self.skip_completed)

        self.apply_settings = None
        if has_settings:
            self.apply_settings = QCheckBox("Also apply the encoding settings saved with this queue")
            layout.addWidget(self.apply_settings)

        buttons = QDialogButtonBox()
        if queue_not_empty:
            replace = QPushButton("Replace Queue")
            append = QPushButton("Add to Queue")
            buttons.addButton(replace, QDialogButtonBox.ButtonRole.AcceptRole)
            buttons.addButton(append, QDialogButtonBox.ButtonRole.AcceptRole)
            replace.clicked.connect(lambda: self._accept_with('replace'))
            append.clicked.connect(lambda: self._accept_with('append'))
        else:
            load = QPushButton("Load")
            load.setDefault(True)
            buttons.addButton(load, QDialogButtonBox.ButtonRole.AcceptRole)
            load.clicked.connect(lambda: self._accept_with('replace'))
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_with(self, mode: str):
        self.mode = mode
        self.accept()


class QueueManager(QObject):
    """Saves, loads and autosaves the input file queue"""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self._restoring = False

    # ---- explicit save / load -----------------------------------------
    def save_queue(self):
        """Save the current queue to a file the user picks"""
        files = self.main_window.file_manager.get_queue()
        if not files:
            QMessageBox.warning(self.main_window, "Empty Queue", "There are no files in the queue to save.")
            return

        default = os.path.join(files[0].directory, "videer-queue.json")
        path, _ = QFileDialog.getSaveFileName(
            self.main_window, "Save Queue", default, QUEUE_FILE_FILTER)
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".json"

        settings = self.main_window.ui_manager.get_current_settings()
        try:
            write_queue_file(path, serialize_queue(files, settings))
        except Exception as e:
            QMessageBox.critical(self.main_window, "Save Failed", f"Failed to save queue: {e}")
            return
        self.main_window.ui_manager.update_status(
            f"Saved {len(files)} file(s) to {os.path.basename(path)}")

    def load_queue(self):
        """Load a queue file the user picks"""
        if self.main_window.process_manager.is_processing():
            QMessageBox.warning(self.main_window, "Processing in Progress",
                                "Stop processing before loading a queue.")
            return

        path, _ = QFileDialog.getOpenFileName(
            self.main_window, "Load Queue", "", QUEUE_FILE_FILTER)
        if not path:
            return

        try:
            data = read_queue_file(path)
        except Exception as e:
            QMessageBox.critical(self.main_window, "Load Failed", f"Failed to read queue file: {e}")
            return

        entries = data['files']
        if not entries:
            QMessageBox.warning(self.main_window, "Empty Queue", "That queue file contains no files.")
            return

        missing = [e for e in entries if not os.path.isfile(e['path'])]
        dialog = QueueLoadDialog(self.main_window, os.path.basename(path), entries, len(missing),
                                 data['settings'] is not None,
                                 bool(self.main_window.file_manager.has_files()))
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.mode:
            return

        skip_completed = bool(dialog.skip_completed and dialog.skip_completed.isChecked())
        if dialog.apply_settings and dialog.apply_settings.isChecked():
            self.main_window.preset_manager.apply_settings(data['settings'])

        added, skipped = self._restore_entries(
            entries, replace=(dialog.mode == 'replace'), skip_completed=skip_completed)

        message = f"Loaded {added} file(s) from {os.path.basename(path)}"
        if skipped:
            message += f" ({skipped} skipped — missing, duplicate or already completed)"
        self.main_window.ui_manager.update_status(message)
        if missing:
            names = ", ".join(os.path.basename(e['path']) for e in missing[:5])
            extra = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
            QMessageBox.information(self.main_window, "Files Not Found",
                                    f"{len(missing)} file(s) from the queue no longer exist and were "
                                    f"skipped:\n\n{names}{extra}")

    # ---- autosave ------------------------------------------------------
    def autosave(self):
        """
        Persist the queue next to the application. Called on every queue change and on exit, so a crash or a
        power cut loses at most the file that was in flight. Failures are silent: an unwritable install
        directory must not interrupt encoding or block the app from closing.
        """
        if self._restoring:
            return
        files = self.main_window.file_manager.get_queue()
        try:
            if not files:
                if os.path.exists(QUEUE_AUTOSAVE_FILE):
                    os.remove(QUEUE_AUTOSAVE_FILE)
                return
            settings = self.main_window.ui_manager.get_current_settings()
            write_queue_file(QUEUE_AUTOSAVE_FILE, serialize_queue(files, settings))
        except Exception:
            pass

    def restore_autosave(self):
        """
        Put back the queue from the last session. Files already encoded are left out — the run would
        otherwise re-encode them — and so are files that have since been deleted or moved.
        """
        if not os.path.exists(QUEUE_AUTOSAVE_FILE):
            return
        try:
            data = read_queue_file(QUEUE_AUTOSAVE_FILE)
        except Exception:
            return

        added, skipped = self._restore_entries(data['files'], replace=True, skip_completed=True)
        if added:
            message = f"Restored {added} file(s) from the previous session"
            if skipped:
                message += f" ({skipped} skipped — missing or already completed)"
            self.main_window.ui_manager.update_status(message)

    # ---- shared ---------------------------------------------------------
    def _restore_entries(self, entries: List[Dict[str, Any]], replace: bool,
                         skip_completed: bool) -> Tuple[int, int]:
        """
        Put queue entries back into the file manager, preserving their saved order, and restore the status of
        the ones that are kept. Returns (added, skipped).
        """
        wanted = [e for e in entries
                  if os.path.isfile(e['path'])
                  and not (skip_completed and e['status'] == 'success')]

        file_manager = self.main_window.file_manager
        self._restoring = True
        try:
            if replace:
                file_manager.clear_queue()
            added = file_manager.add_files([e['path'] for e in wanted], preserve_order=True)

            # Re-attach the saved status so a restored queue still shows what already succeeded or failed
            status_by_path = {canonical_path(e['path']): e['status'] for e in wanted}
            for file in file_manager.get_queue():
                status = status_by_path.get(file.canonical)
                if status and status != 'running':      # an interrupted file is pending again, not running
                    file.status = status
        finally:
            self._restoring = False

        file_manager.refresh()
        self.autosave()
        return added, len(entries) - added
