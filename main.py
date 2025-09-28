#!/usr/bin/env python3
"""
videer - Main Application
Modular video processing application with FFmpeg and AviSynth+ support
"""

import sys
import os
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
from PySide6.QtCore import QSettings
from PySide6.QtGui import QPalette, QColor

# Import modules
from modules.ui_manager import UIManager
from modules.file_manager import FileManager
from modules.process_manager import ProcessManager
from modules.preset_manager import PresetManager
from utils.ffmpeg_utils import check_ffmpeg_status
from config import APP_NAME, APP_VERSION, WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT


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
        self.ui_manager = UIManager(self)
        
        # Setup UI
        self.ui_manager.setup_ui()
        
        # Connect signals
        self.connect_signals()
        
        # Load saved settings
        self.load_settings()
        
        # Check FFmpeg status
        self.check_dependencies()
    
    def connect_signals(self):
        """Connect all signals between managers"""
        # File manager signals
        self.file_manager.files_updated.connect(self.ui_manager.update_file_list)
        self.file_manager.file_count_changed.connect(self.ui_manager.update_file_count)
        
        # Process manager signals
        self.process_manager.progress_updated.connect(self.ui_manager.update_progress)
        self.process_manager.status_updated.connect(self.ui_manager.update_status)
        self.process_manager.processing_finished.connect(self.on_processing_finished)
        
        # UI signals
        self.ui_manager.start_processing.connect(self.start_processing)
        self.ui_manager.stop_processing.connect(self.stop_processing)
        self.ui_manager.files_added.connect(self.file_manager.add_files)
        self.ui_manager.files_removed.connect(self.file_manager.remove_files)
        self.ui_manager.queue_cleared.connect(self.file_manager.clear_queue)
    
    def check_dependencies(self):
        """Check if required dependencies are available"""
        ffmpeg_available = check_ffmpeg_status()
        self.ui_manager.update_ffmpeg_status(ffmpeg_available)
        
        if not ffmpeg_available:
            QMessageBox.warning(
                self,
                "FFmpeg Not Found",
                "FFmpeg was not found in your system PATH or current directory.\n"
                "Please install FFmpeg or place ffmpeg.exe in the application directory."
            )
    
    def start_processing(self):
        """Start processing the file queue"""
        if not self.file_manager.has_files():
            QMessageBox.warning(self, "No Files", "Please add files to process.")
            return
        
        settings = self.ui_manager.get_current_settings()
        files = self.file_manager.get_queue()
        
        self.process_manager.start_processing(files, settings)
        self.ui_manager.set_processing_state(True)
    
    def stop_processing(self):
        """Stop the current processing"""
        self.process_manager.stop_processing()
        self.ui_manager.set_processing_state(False)
    
    def on_processing_finished(self, success_count, total_count):
        """Handle processing completion"""
        self.ui_manager.set_processing_state(False)
        
        QMessageBox.information(
            self,
            "Processing Complete",
            f"Successfully processed {success_count} of {total_count} files."
        )
    
    def load_settings(self):
        """Load saved application settings"""
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
        event.accept()


def main():
    """Main entry point"""
    app = QApplication(sys.argv)
    
    # Set application style
    app.setStyle('Fusion')
    
    # Optional: Enable dark theme
    # setup_dark_theme(app)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


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