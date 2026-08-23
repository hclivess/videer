#!/usr/bin/env python3
"""
videer - Main Application
Modular video processing application with FFmpeg and AviSynth+ support
"""

import sys
import os
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
from PySide6.QtCore import QSettings
from PySide6.QtGui import QPalette, QColor, QIcon, QDragEnterEvent, QDragMoveEvent, QDropEvent

# Import modules
from modules.ui_manager import UIManager
from modules.file_manager import FileManager
from modules.process_manager import ProcessManager
from modules.preset_manager import PresetManager
from modules.queue_manager import QueueManager
from utils.ffmpeg_utils import check_ffmpeg_status
from config import APP_NAME, APP_VERSION, WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT
from utils import childproc


class MainWindow(QMainWindow):
    """Main application window"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setAcceptDrops(True)
        self.settings = QSettings(APP_NAME, "Settings")
        
        # Set minimum window size
        self.setMinimumWidth(WINDOW_MIN_WIDTH)
        self.setMinimumHeight(WINDOW_MIN_HEIGHT)
        
        # Initialize managers
        self.file_manager = FileManager()
        self.process_manager = ProcessManager(self)
        self.preset_manager = PresetManager(self)
        self.queue_manager = QueueManager(self)   # before the UI: the File menu binds to it
        self.ui_manager = UIManager(self)
        
        # Setup UI
        self.ui_manager.setup_ui()
        
        # Connect signals
        self.connect_signals()
        
        # Load saved settings
        self.load_settings()

        # Put back the queue from the previous session (skipping what already encoded)
        self.queue_manager.restore_autosave()
        
        # Check FFmpeg status
        self.check_dependencies()
    
    def connect_signals(self):
        """Connect all signals between managers"""
        # File manager signals
        self.file_manager.files_updated.connect(self.ui_manager.update_file_list)
        self.file_manager.file_count_changed.connect(self.ui_manager.update_file_count)
        self.file_manager.duplicates_skipped.connect(self._on_duplicates_skipped)
        
        # Process manager signals
        self.process_manager.progress_updated.connect(self.ui_manager.update_progress)
        self.process_manager.status_updated.connect(self.ui_manager.update_status)
        self.process_manager.stats_updated.connect(self.ui_manager.update_stats)
        self.process_manager.file_state_changed.connect(self.ui_manager.set_file_state)
        self.process_manager.vmaf_calculated.connect(self.ui_manager.set_file_vmaf)
        self.process_manager.processing_finished.connect(self.on_processing_finished)
        
        # UI signals
        self.ui_manager.start_processing.connect(self.start_processing)
        self.ui_manager.stop_processing.connect(self.stop_processing)
        self.ui_manager.pause_clicked.connect(self.toggle_pause)
        self.process_manager.paused_state_changed.connect(self.ui_manager.set_paused_state)
        self.ui_manager.files_added.connect(self.file_manager.add_files)
        self.ui_manager.files_removed.connect(self._on_files_removed)
        self.ui_manager.queue_cleared.connect(self.file_manager.clear_queue)
        self.ui_manager.files_reordered.connect(self.file_manager.move_file)

        # Route newly-added files to the running process queue
        self.file_manager.files_updated.connect(self._on_files_updated_during_processing)

        # Keep the on-disk queue snapshot current: additions, removals, reordering and per-file results all
        # land in it, so a crash or a power cut costs at most the file that was in flight.
        self.file_manager.files_updated.connect(lambda _files: self.queue_manager.autosave())
        self.process_manager.file_state_changed.connect(lambda *_: self.queue_manager.autosave())
    
    def check_dependencies(self):
        """Check if required dependencies are available"""
        ffmpeg_available = check_ffmpeg_status()
        self.ui_manager.update_ffmpeg_status(ffmpeg_available)
        
        if not ffmpeg_available:
            QMessageBox.warning(
                self,
                "FFmpeg Not Found",
                "FFmpeg was not found in your system PATH or the application directory.\n"
                "Install FFmpeg (or place the ffmpeg binary next to main.py)."
            )
    
    def start_processing(self):
        """Start processing the file queue"""
        if not self.file_manager.has_files():
            QMessageBox.warning(self, "No Files", "Please add files to process.")
            return
        
        settings = self.ui_manager.get_current_settings()
        files = self.file_manager.get_queue()

        issues = self.process_manager.validate_settings(settings)
        if issues:
            reply = QMessageBox.warning(
                self,
                "Check Settings",
                "The current settings have potential problems:\n\n• "
                + "\n• ".join(issues)
                + "\n\nStart processing anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self.process_manager.start_processing(files, settings)
        self.ui_manager.set_processing_state(True)
    
    def stop_processing(self):
        """
        Ask the run to stop. The UI is *not* switched back to idle here — that happens in
        on_processing_finished, once the worker has actually unwound. Reporting "stopped" while FFmpeg is
        still alive is what let a run keep the CPU busy behind a UI that claimed to be done.
        """
        self.process_manager.stop_processing()

    def toggle_pause(self):
        """Pause or resume the current processing run"""
        if self.process_manager.is_paused():
            self.process_manager.resume_processing()
        else:
            self.process_manager.pause_processing()
    
    # ---- drag & drop onto the window ------------------------------------
    # setAcceptDrops(True) on its own only makes the window *look* like a drop target: without these
    # handlers every drop that misses the file list is discarded. All three are needed — a drag whose
    # dragMoveEvent is not accepted is refused by the platform and never produces a drop.
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent):
        """Files dropped anywhere on the window join the queue — during a run too (they are appended)"""
        if not event.mimeData().hasUrls():
            super().dropEvent(event)
            return
        event.acceptProposedAction()
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.file_manager.add_files(paths)

    def _on_files_updated_during_processing(self, files):
        """Keep the running queue's pending tail in sync with the UI queue"""
        if self.process_manager.is_processing():
            self.process_manager.sync_pending(files)

    def _on_duplicates_skipped(self, paths):
        """Tell the user which dropped/selected files were already in the queue"""
        names = [os.path.basename(p) for p in paths]
        shown = ", ".join(names[:5]) + (f" (+{len(names) - 5} more)" if len(names) > 5 else "")
        self.ui_manager.update_status(
            f"Skipped {len(names)} duplicate file{'s' if len(names) != 1 else ''} already in queue: {shown}")

    def _on_files_removed(self, indices):
        """Safe removal: block removal of already-processed or in-progress files"""
        if self.process_manager.is_processing():
            current_index = self.process_manager.current_file_index
            safe = [i for i in indices if i > current_index]
            blocked = [i for i in indices if i <= current_index]
            if blocked:
                QMessageBox.warning(self, "Cannot Remove",
                    f"{len(blocked)} file(s) already processed or in progress.")
            if safe:
                self.file_manager.remove_files(safe)
        else:
            self.file_manager.remove_files(indices)

    def on_processing_finished(self, success_count, total_count):
        """Handle processing completion — the single place the UI returns to idle"""
        self.ui_manager.set_processing_state(False)

        QMessageBox.information(
            self,
            "Processing Complete",
            f"Successfully processed {success_count} of {total_count} files."
        )
    
    def load_settings(self):
        """Load settings in priority order: factory → defaults.json → QSettings"""
        # 1. Factory defaults already set by UI control initializers
        # 2. Apply user defaults from defaults.json (if exists)
        user_defaults = self.preset_manager.load_defaults()
        if user_defaults:
            self.preset_manager.apply_settings(user_defaults)
        # 3. Apply last-session state from QSettings (overrides defaults.json)
        self.ui_manager.load_settings(self.settings)
    
    def save_settings(self):
        """Save current application settings"""
        self.ui_manager.save_settings(self.settings)
    
    def closeEvent(self, event):
        """Handle application close event"""
        if self.process_manager.is_processing():
            reply = QMessageBox.question(
                self,
                "Processing in Progress",
                "Processing is still in progress. Are you sure you want to exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
            
            self.process_manager.stop_processing()
        
        self.save_settings()
        self.queue_manager.autosave()
        event.accept()


def main():
    """Main entry point"""
    app = QApplication(sys.argv)
    childproc.install_qt_hook(app)
    
    # Set application style
    app.setStyle('Fusion')

    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.ico')
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    
    # Optional: Enable dark theme
    # setup_dark_theme(app)
    
    window = MainWindow()
    window.show()
    selftest = os.environ.get("VIDEER_SELFTEST")
    if selftest:
        _run_selftest(window, selftest)
    sys.exit(app.exec())


def _run_selftest(window: "MainWindow", path: str):
    """
    Headless smoke test used by CI: encode one clip with libx264/aac and exit 0 only if the output exists under
    its final name, is non-empty, and no .part file was left behind.
    """
    from PySide6.QtCore import QTimer
    window.preset_manager.apply_settings({"video_codec": "libx264", "audio_codec": "aac", "crf": 30, "abr": 96,
                                          "preset": "ultrafast", "output_format": "mkv", "replace_files": False,
                                          "delete_source": False, "calculate_vmaf": False, "use_avisynth": False,
                                          "transcode_video": False, "transcode_audio": False})
    window.file_manager.add_files([path])
    window.process_manager.status_updated.connect(lambda m: print("selftest:", m, flush=True))

    def finished(ok, total):
        files = window.file_manager.get_queue()
        out = files[0].get_full_output_path() if files else ""
        part = files[0].get_temp_output_path() if files else ""
        good = (ok == total == 1 and os.path.isfile(out) and os.path.getsize(out) > 0 and not os.path.exists(part))
        print(f"selftest: finished {ok}/{total}, output={out} exists={os.path.isfile(out)} part_left={os.path.exists(part)}"
              f" : {'ok' if good else 'FAILED'}", flush=True)
        QTimer.singleShot(0, lambda: sys.exit(0 if good else 1))
    window.process_manager.processing_finished.disconnect(window.on_processing_finished)
    window.process_manager.processing_finished.connect(finished)
    QTimer.singleShot(300, lambda: window.process_manager.start_processing(
        window.file_manager.get_queue(), window.ui_manager.get_current_settings()))
    QTimer.singleShot(600_000, lambda: (print("selftest: timeout", flush=True), sys.exit(1)))


def setup_dark_theme(app):
    """Setup dark theme for the application"""
    dark_palette = QPalette()
    dark_palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.ColorRole.WindowText, QColor(255, 255, 255))
    dark_palette.setColor(QPalette.ColorRole.Base, QColor(25, 25, 25))
    dark_palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(0, 0, 0))
    dark_palette.setColor(QPalette.ColorRole.ToolTipText, QColor(255, 255, 255))
    dark_palette.setColor(QPalette.ColorRole.Text, QColor(255, 255, 255))
    dark_palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.ColorRole.ButtonText, QColor(255, 255, 255))
    dark_palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
    dark_palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    dark_palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    dark_palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
    app.setPalette(dark_palette)


if __name__ == "__main__":
    main()