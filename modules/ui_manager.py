"""
UI Manager for videer
Handles all UI creation and management
"""

from typing import Dict, Any, List
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                              QCheckBox, QRadioButton, QPushButton, QLineEdit, 
                              QSlider, QTextEdit, QFileDialog, QButtonGroup, 
                              QGroupBox, QListWidget, QStyle, QProgressBar, 
                              QSplitter, QMenuBar, QMenu, QStatusBar, QListWidgetItem,
                              QTabWidget, QSpinBox, QComboBox, QGridLayout, QFrame)
from PySide6.QtCore import Qt, Signal, QSettings
from PySide6.QtGui import QAction, QIcon, QDragEnterEvent, QDropEvent

from config import (VIDEO_CODECS, AUDIO_CODECS, OUTPUT_FORMATS, 
                   ENCODING_PRESETS, PAR_PRESETS, DAR_PRESETS,
                   DEFAULT_CRF, DEFAULT_ABR, MAX_THREADS)


class FileListWidget(QListWidget):
    """Custom list widget with drag and drop support"""
    
    files_dropped = Signal(list)
    
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self._setup_style()
    
    def _setup_style(self):
        self.setStyleSheet("""
            QListWidget {
                border: 2px solid #aaa;
                border-radius: 5px;
                padding: 5px;
                background-color: #f9f9f9;
            }
            QListWidget::item {
                border-bottom: 1px solid #eee;
                padding: 8px;
                margin: 2px;
                border-radius: 3px;
            }
            QListWidget::item:selected {
                background-color: #0078D7;
                color: white;
            }
            QListWidget::item:alternate {
                background-color: #f7f7f7;
            }
            QListWidget::item:hover {
                background-color: #e3f2fd;
            }
        """)
    
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            super().dragEnterEvent(event)
    
    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            event.accept()
            links = []
            for url in event.mimeData().urls():
                links.append(url.toLocalFile())
            self.files_dropped.emit(links)
        else:
            super().dropEvent(event)


class UIManager(QWidget):
    """Main UI Manager"""
    
    # Signals
    start_processing = Signal()
    stop_processing = Signal()
    files_added = Signal(list)
    files_removed = Signal(list)
    queue_cleared = Signal()
    settings_changed = Signal(dict)
    
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.codec_groups = {}
        self.controls = {}
        
    def setup_ui(self):
        """Initialize the complete UI"""
        self._create_menu_bar()
        self._create_main_layout()
        self._create_status_bar()
        
    def _create_menu_bar(self):
        """Create menu bar"""
        menubar = self.main_window.menuBar()
        
        # File menu
        file_menu = menubar.addMenu('File')
        
        self._add_action(file_menu, 'Add Files', 'Ctrl+O', self._on_add_files)
        self._add_action(file_menu, 'Add Folder', 'Ctrl+Shift+O', self._on_add_folder)
        file_menu.addSeparator()
        self._add_action(file_menu, 'Clear Queue', None, self._on_clear_queue)
        file_menu.addSeparator()
        self._add_action(file_menu, 'Exit', 'Ctrl+Q', self.main_window.close)
        
        # Presets menu
        presets_menu = menubar.addMenu('Presets')
        self._add_action(presets_menu, 'Save Current Settings', None, 
                        self.main_window.preset_manager.save_preset)
        self._add_action(presets_menu, 'Load Preset', None, 
                        self.main_window.preset_manager.load_preset)
        presets_menu.addSeparator()
        
        # Default presets
        self._add_action(presets_menu, 'Web Quality (H.264/AAC)', None, 
                        lambda: self.main_window.preset_manager.apply_preset('web'))
        self._add_action(presets_menu, 'High Quality (H.265/Opus)', None,
                        lambda: self.main_window.preset_manager.apply_preset('hq'))
        self._add_action(presets_menu, 'Archive (ProRes/PCM)', None,
                        lambda: self.main_window.preset_manager.apply_preset('archive'))
        
        # Help menu
        help_menu = menubar.addMenu('Help')
        self._add_action(help_menu, 'About', None, self._show_about)
    
    def _add_action(self, menu, text, shortcut, slot):
        """Helper to add menu action"""
        action = QAction(text, self.main_window)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action
    
    def _create_main_layout(self):
        """Create main layout with splitter"""
        main_widget = QWidget()
        self.main_window.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)
        
        # Left panel
        left_panel = self._create_left_panel()
        splitter.addWidget(left_panel)
        
        # Right panel with tabs
        right_panel = self._create_right_panel()
        splitter.addWidget(right_panel)
        
        splitter.setSizes([500, 700])
    
    def _create_left_panel(self):
        """Create left panel with file list and controls"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        
        # File list
        files_group = QGroupBox("Input Files")
        files_layout = QVBoxLayout()
        
        self.file_count_label = QLabel("0 files in queue")
        files_layout.addWidget(self.file_count_label)
        
        self.file_list = FileListWidget()
        self.file_list.files_dropped.connect(self.files_added)
        files_layout.addWidget(self.file_list)
        
        # File controls
        file_controls = QHBoxLayout()
        
        self.controls['add_files'] = QPushButton("Add Files")
        self.controls['add_files'].clicked.connect(self._on_add_files)
        
        self.controls['add_folder'] = QPushButton("Add Folder")
        self.controls['add_folder'].clicked.connect(self._on_add_folder)
        
        self.controls['remove_files'] = QPushButton("Remove")
        self.controls['remove_files'].clicked.connect(self._on_remove_files)
        
        self.controls['clear_files'] = QPushButton("Clear All")
        self.controls['clear_files'].clicked.connect(self._on_clear_queue)
        
        file_controls.addWidget(self.controls['add_files'])
        file_controls.addWidget(self.controls['add_folder'])
        file_controls.addWidget(self.controls['remove_files'])
        file_controls.addWidget(self.controls['clear_files'])
        
        files_layout.addLayout(file_controls)
        files_group.setLayout(files_layout)
        layout.addWidget(files_group)
        
        # Progress
        progress_group = QGroupBox("Progress")
        progress_layout = QVBoxLayout()
        
        self.progress_bar = QProgressBar()
        progress_layout.addWidget(self.progress_bar)
        
        self.status_label = QLabel("Ready")
        progress_layout.addWidget(self.status_label)
        
        self.time_label = QLabel("")
        progress_layout.addWidget(self.time_label)
        
        progress_group.setLayout(progress_layout)
        layout.addWidget(progress_group)
        
        # Action buttons
        buttons_layout = QHBoxLayout()
        
        self.controls['start'] = QPushButton("Start Processing")
        self.controls['start'].clicked.connect(self.start_processing)
        self.controls['start'].setStyleSheet("""
            QPushButton {
                font-size: 14px;
                font-weight: bold;
                padding: 8px;
                background-color: #28a745;
                color: white;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #218838;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        
        self.controls['stop'] = QPushButton("Stop")
        self.controls['stop'].clicked.connect(self.stop_processing)
        self.controls['stop'].setEnabled(False)
        self.controls['stop'].setStyleSheet("""
            QPushButton {
                font-size: 14px;
                font-weight: bold;
                padding: 8px;
                background-color: #dc3545;
                color: white;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #c82333;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        
        buttons_layout.addWidget(self.controls['start'])
        buttons_layout.addWidget(self.controls['stop'])
        
        layout.addLayout(buttons_layout)
        layout.addStretch()
        
        return panel
    
    def _create_right_panel(self):
        """Create right panel with settings tabs"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        
        self.tabs = QTabWidget()
        
        # Create tabs
        self.tabs.addTab(self._create_video_tab(), "Video")
        self.tabs.addTab(self._create_audio_tab(), "Audio")
        self.tabs.addTab(self._create_processing_tab(), "Processing")
        self.tabs.addTab(self._create_advanced_tab(), "Advanced")
        self.tabs.addTab(self._create_output_tab(), "Output")
        
        layout.addWidget(self.tabs)
        
        return panel
    
    def _create_video_tab(self):
        """Create video settings tab"""
        widget = QWidget()
        layout = QVBoxLayout()
        
        # Video codec
        codec_group = QGroupBox("Video Codec")
        codec_layout = QVBoxLayout()
        
        self.codec_groups['video'] = QButtonGroup()
        for text, value in VIDEO_CODECS:
            radio = QRadioButton(text)
            radio.setProperty("value", value)
            self.codec_groups['video'].addButton(radio)
            codec_layout.addWidget(radio)
            if value == "libx265":
                radio.setChecked(True)
        
        codec_group.setLayout(codec_layout)
        layout.addWidget(codec_group)
        
        # Quality settings
        quality_group = QGroupBox("Quality Settings")
        quality_layout = QGridLayout()
        
        quality_layout.addWidget(QLabel("Encoding Speed:"), 0, 0)
        self.controls['preset'] = QComboBox()
        self.controls['preset'].addItems(ENCODING_PRESETS)
        self.controls['preset'].setCurrentIndex(5)
        quality_layout.addWidget(self.controls['preset'], 0, 1)
        
        quality_layout.addWidget(QLabel("CRF (Quality):"), 1, 0)
        crf_widget = QWidget()
        crf_layout = QHBoxLayout(crf_widget)
        crf_layout.setContentsMargins(0, 0, 0, 0)
        
        self.controls['crf_slider'] = QSlider(Qt.Orientation.Horizontal)
        self.controls['crf_slider'].setRange(0, 51)
        self.controls['crf_slider'].setValue(DEFAULT_CRF)
        
        self.controls['crf'] = QSpinBox()
        self.controls['crf'].setRange(0, 51)
        self.controls['crf'].setValue(DEFAULT_CRF)
        
        self.controls['crf_slider'].valueChanged.connect(self.controls['crf'].setValue)
        self.controls['crf'].valueChanged.connect(self.controls['crf_slider'].setValue)
        
        crf_layout.addWidget(self.controls['crf_slider'])
        crf_layout.addWidget(self.controls['crf'])
        quality_layout.addWidget(crf_widget, 1, 1)
        
        quality_group.setLayout(quality_layout)
        layout.addWidget(quality_group)
        
        # PAR settings
        par_group = QGroupBox("Pixel Aspect Ratio (PAR)")
        par_layout = QGridLayout()
        
        par_layout.addWidget(QLabel("PAR Mode:"), 0, 0)
        self.controls['par_mode'] = QComboBox()
        self.controls['par_mode'].addItems(list(PAR_PRESETS.keys()))
        self.controls['par_mode'].currentTextChanged.connect(self._on_par_mode_changed)
        par_layout.addWidget(self.controls['par_mode'], 0, 1)
        
        par_layout.addWidget(QLabel("PAR Handling:"), 1, 0)
        self.controls['par_handling'] = QComboBox()
        self.controls['par_handling'].addItems([
            "Metadata Only (Faster)",
            "Resample to Square Pixels",
            "Preserve Original"
        ])
        self.controls['par_handling'].setToolTip(
            "Metadata: Just sets display flags (faster)\n"
            "Resample: Actually converts pixels to square (better compatibility)\n"
            "Preserve: Keep original PAR unchanged"
        )
        par_layout.addWidget(self.controls['par_handling'], 1, 1)
        
        self.controls['par_custom'] = QLineEdit("1:1")
        self.controls['par_custom'].setEnabled(False)
        par_layout.addWidget(QLabel("Custom PAR:"), 2, 0)
        par_layout.addWidget(self.controls['par_custom'], 2, 1)
        
        par_group.setLayout(par_layout)
        layout.addWidget(par_group)
        
        layout.addStretch()
        widget.setLayout(layout)
        return widget
    
    def _create_audio_tab(self):
        """Create audio settings tab"""
        widget = QWidget()
        layout = QVBoxLayout()
        
        # Audio codec
        codec_group = QGroupBox("Audio Codec")
        codec_layout = QVBoxLayout()
        
        self.codec_groups['audio'] = QButtonGroup()
        for text, value in AUDIO_CODECS:
            radio = QRadioButton(text)
            radio.setProperty("value", value)
            self.codec_groups['audio'].addButton(radio)
            codec_layout.addWidget(radio)
            if value == "aac":
                radio.setChecked(True)
        
        codec_group.setLayout(codec_layout)
        layout.addWidget(codec_group)
        
        # Audio settings
        settings_group = QGroupBox("Audio Settings")
        settings_layout = QGridLayout()
        
        settings_layout.addWidget(QLabel("Bitrate:"), 0, 0)
        abr_widget = QWidget()
        abr_layout = QHBoxLayout(abr_widget)
        abr_layout.setContentsMargins(0, 0, 0, 0)
        
        self.controls['abr_slider'] = QSlider(Qt.Orientation.Horizontal)
        self.controls['abr_slider'].setRange(32, 512)
        self.controls['abr_slider'].setValue(DEFAULT_ABR)
        
        self.controls['abr'] = QSpinBox()
        self.controls['abr'].setRange(32, 512)
        self.controls['abr'].setValue(DEFAULT_ABR)
        self.controls['abr'].setSuffix(" kbps")
        
        self.controls['abr_slider'].valueChanged.connect(self.controls['abr'].setValue)
        self.controls['abr'].valueChanged.connect(self.controls['abr_slider'].setValue)
        
        abr_layout.addWidget(self.controls['abr_slider'])
        abr_layout.addWidget(self.controls['abr'])
        settings_layout.addWidget(abr_widget, 0, 1)
        
        self.controls['stereo'] = QCheckBox("Force Stereo (2.0)")
        settings_layout.addWidget(self.controls['stereo'], 1, 1)
        
        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)
        
        layout.addStretch()
        widget.setLayout(layout)
        return widget
    
    def _create_processing_tab(self):
        """Create processing tab"""
        widget = QWidget()
        layout = QVBoxLayout()
        
        # Deinterlacing
        deint_group = QGroupBox("Deinterlacing")
        deint_layout = QVBoxLayout()
        
        self.controls['deinterlace'] = QCheckBox("Enable Deinterlacing")
        self.controls['tff'] = QCheckBox("Top Field First")
        self.controls['reduce_fps'] = QCheckBox("Reduce Frame Rate (Halve FPS)")
        
        deint_layout.addWidget(self.controls['deinterlace'])
        deint_layout.addWidget(self.controls['tff'])
        deint_layout.addWidget(self.controls['reduce_fps'])
        
        deint_group.setLayout(deint_layout)
        layout.addWidget(deint_group)
        
        # AviSynth
        avs_group = QGroupBox("AviSynth+ Processing")
        avs_layout = QVBoxLayout()
        
        self.controls['use_avisynth'] = QCheckBox("Use AviSynth+")
        self.controls['use_ffms2'] = QCheckBox("Use FFMS2 Source Filter")
        
        avs_layout.addWidget(self.controls['use_avisynth'])
        avs_layout.addWidget(self.controls['use_ffms2'])
        
        avs_layout.addWidget(QLabel("Custom AviSynth Script:"))
        self.controls['avisynth_extras'] = QTextEdit()
        self.controls['avisynth_extras'].setMaximumHeight(100)
        avs_layout.addWidget(self.controls['avisynth_extras'])
        
        avs_group.setLayout(avs_layout)
        layout.addWidget(avs_group)
        
        # Pre-processing
        preprocess_group = QGroupBox("Pre-processing")
        preprocess_layout = QVBoxLayout()
        
        self.controls['transcode_video'] = QCheckBox("Transcode to Raw Video First")
        self.controls['transcode_audio'] = QCheckBox("Transcode to Raw Audio First")
        
        preprocess_layout.addWidget(self.controls['transcode_video'])
        preprocess_layout.addWidget(self.controls['transcode_audio'])
        
        preprocess_group.setLayout(preprocess_layout)
        layout.addWidget(preprocess_group)
        
        layout.addStretch()
        widget.setLayout(layout)
        return widget
    
    def _create_advanced_tab(self):
        """Create advanced settings tab"""
        widget = QWidget()
        layout = QVBoxLayout()
        
        # DAR settings
        dar_group = QGroupBox("Display Aspect Ratio (DAR)")
        dar_layout = QGridLayout()
        
        dar_layout.addWidget(QLabel("DAR Mode:"), 0, 0)
        self.controls['dar_mode'] = QComboBox()
        self.controls['dar_mode'].addItems(list(DAR_PRESETS.keys()))
        self.controls['dar_mode'].currentTextChanged.connect(self._on_dar_mode_changed)
        dar_layout.addWidget(self.controls['dar_mode'], 0, 1)
        
        self.controls['dar_custom'] = QLineEdit("16:9")
        self.controls['dar_custom'].setEnabled(False)
        dar_layout.addWidget(QLabel("Custom DAR:"), 1, 0)
        dar_layout.addWidget(self.controls['dar_custom'], 1, 1)
        
        dar_group.setLayout(dar_layout)
        layout.addWidget(dar_group)
        
        # Fixes
        fixes_group = QGroupBox("Fixes & Workarounds")
        fixes_layout = QVBoxLayout()
        
        self.controls['corrupt_fix'] = QCheckBox("Fix H.264 Stream Corruption (TS files)")
        fixes_layout.addWidget(self.controls['corrupt_fix'])
        
        fixes_group.setLayout(fixes_layout)
        layout.addWidget(fixes_group)
        
        # FFmpeg extras
        ffmpeg_group = QGroupBox("FFmpeg Options")
        ffmpeg_layout = QVBoxLayout()
        
        ffmpeg_layout.addWidget(QLabel("Additional FFmpeg Parameters:"))
        self.controls['ffmpeg_extras'] = QLineEdit()
        ffmpeg_layout.addWidget(self.controls['ffmpeg_extras'])
        
        ffmpeg_group.setLayout(ffmpeg_layout)
        layout.addWidget(ffmpeg_group)
        
        # Performance
        perf_group = QGroupBox("Performance")
        perf_layout = QGridLayout()
        
        perf_layout.addWidget(QLabel("CPU Threads:"), 0, 0)
        self.controls['threads'] = QSpinBox()
        self.controls['threads'].setRange(1, MAX_THREADS)
        self.controls['threads'].setValue(MAX_THREADS)
        perf_layout.addWidget(self.controls['threads'], 0, 1)
        
        perf_group.setLayout(perf_layout)
        layout.addWidget(perf_group)
        
        layout.addStretch()
        widget.setLayout(layout)
        return widget
    
    def _create_output_tab(self):
        """Create output settings tab"""
        widget = QWidget()
        layout = QVBoxLayout()
        
        # Output format
        format_group = QGroupBox("Output Format")
        format_layout = QGridLayout()
        
        format_layout.addWidget(QLabel("Container:"), 0, 0)
        self.controls['output_format'] = QComboBox()
        self.controls['output_format'].addItems(OUTPUT_FORMATS)
        format_layout.addWidget(self.controls['output_format'], 0, 1)
        
        format_group.setLayout(format_layout)
        layout.addWidget(format_group)
        
        # File handling
        file_group = QGroupBox("File Handling")
        file_layout = QVBoxLayout()
        
        self.controls['replace_files'] = QCheckBox("Replace Original Files")
        self.controls['replace_files'].setStyleSheet("color: #d9534f; font-weight: bold;")
        file_layout.addWidget(self.controls['replace_files'])
        
        file_group.setLayout(file_layout)
        layout.addWidget(file_group)
        
        layout.addStretch()
        widget.setLayout(layout)
        return widget
    
    def _create_status_bar(self):
        """Create status bar"""
        self.status_bar = QStatusBar()
        self.main_window.setStatusBar(self.status_bar)
        
        self.ffmpeg_status = QLabel("FFmpeg: Checking...")
        self.status_bar.addPermanentWidget(self.ffmpeg_status)
    
    def _on_par_mode_changed(self, text):
        """Handle PAR mode change"""
        self.controls['par_custom'].setEnabled(text == "Custom")
    
    def _on_dar_mode_changed(self, text):
        """Handle DAR mode change"""
        self.controls['dar_custom'].setEnabled(text == "Custom")
    
    def _on_add_files(self):
        """Add files dialog"""
        from PySide6.QtWidgets import QFileDialog
        files, _ = QFileDialog.getOpenFileNames(
            self.main_window,
            "Select Input Files",
            "",
            "Video Files (*.mp4 *.avi *.mkv *.mov *.wmv *.flv *.webm);;All Files (*.*)"
        )
        if files:
            self.files_added.emit(files)
    
    def _on_add_folder(self):
        """Add folder dialog"""
        from PySide6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(self.main_window, "Select Folder")
        if folder:
            self.main_window.file_manager.add_folder(folder)
    
    def _on_remove_files(self):
        """Remove selected files"""
        selected = [self.file_list.row(item) for item in self.file_list.selectedItems()]
        if selected:
            self.files_removed.emit(selected)
    
    def _on_clear_queue(self):
        """Clear file queue"""
        self.queue_cleared.emit()
    
    def _show_about(self):
        """Show about dialog"""
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.about(
            self.main_window,
            "About videer",
            "videer v2.1\n\n"
            "Professional video processing with:\n"
            "• Multi-format support\n"
            "• Hardware acceleration\n"
            "• AviSynth+ integration\n"
            "• PAR/DAR support\n"
            "• Batch processing\n\n"
            "Drag and drop files or folders to process."
        )
    
    def update_file_list(self, files):
        """Update file list display"""
        self.file_list.clear()
        for file in files:
            item = QListWidgetItem(f"{file.filename} ({file.get_file_size_mb():.1f} MB)")
            item.setToolTip(file.filepath)
            self.file_list.addItem(item)
    
    def update_file_count(self, count):
        """Update file count label"""
        self.file_count_label.setText(f"{count} files in queue")
        self.controls['start'].setEnabled(count > 0)
    
    def update_progress(self, value, maximum):
        """Update progress bar"""
        self.progress_bar.setMaximum(maximum)
        self.progress_bar.setValue(value)
    
    def update_status(self, message):
        """Update status label"""
        self.status_label.setText(message)
    
    def update_ffmpeg_status(self, available):
        """Update FFmpeg status in status bar"""
        if available:
            self.ffmpeg_status.setText("FFmpeg: ✓ Found")
            self.ffmpeg_status.setStyleSheet("color: green;")
        else:
            self.ffmpeg_status.setText("FFmpeg: ✗ Not Found")
            self.ffmpeg_status.setStyleSheet("color: red;")
    
    def set_processing_state(self, is_processing):
        """Set UI state for processing"""
        self.controls['start'].setEnabled(not is_processing)
        self.controls['stop'].setEnabled(is_processing)
        self.controls['add_files'].setEnabled(not is_processing)
        self.controls['add_folder'].setEnabled(not is_processing)
        self.tabs.setEnabled(not is_processing)
    
    def get_current_settings(self) -> Dict[str, Any]:
        """Get all current settings"""
        settings = {
            'video_codec': self._get_selected_codec('video'),
            'audio_codec': self._get_selected_codec('audio'),
            'crf': self.controls['crf'].value(),
            'abr': self.controls['abr'].value(),
            'preset': self.controls['preset'].currentText(),
            'output_format': self.controls['output_format'].currentText(),
            'stereo': self.controls['stereo'].isChecked(),
            'deinterlace': self.controls['deinterlace'].isChecked(),
            'tff': self.controls['tff'].isChecked(),
            'reduce_fps': self.controls['reduce_fps'].isChecked(),
            'use_avisynth': self.controls['use_avisynth'].isChecked(),
            'use_ffms2': self.controls['use_ffms2'].isChecked(),
            'transcode_video': self.controls['transcode_video'].isChecked(),
            'transcode_audio': self.controls['transcode_audio'].isChecked(),
            'corrupt_fix': self.controls['corrupt_fix'].isChecked(),
            'replace_files': self.controls['replace_files'].isChecked(),
            'threads': self.controls['threads'].value(),
            'ffmpeg_extras': self.controls['ffmpeg_extras'].text(),
            'avisynth_extras': self.controls['avisynth_extras'].toPlainText(),
            'par_mode': self.controls['par_mode'].currentText(),
            'par_custom': self.controls['par_custom'].text(),
            'dar_mode': self.controls['dar_mode'].currentText(),
            'dar_custom': self.controls['dar_custom'].text()
        }
        
        # Get PAR handling mode
        par_handling_text = self.controls['par_handling'].currentText()
        if "Metadata" in par_handling_text:
            settings['par_handling'] = 'metadata'
        elif "Resample" in par_handling_text:
            settings['par_handling'] = 'resample'
        else:
            settings['par_handling'] = 'preserve'
        
        # Get PAR/DAR values
        if settings['par_mode'] != 'Custom':
            settings['par_value'] = PAR_PRESETS.get(settings['par_mode'], '1:1')
        if settings['dar_mode'] != 'Custom':
            settings['dar_value'] = DAR_PRESETS.get(settings['dar_mode'], 'auto')
        
        return settings
    
    def _get_selected_codec(self, group_name):
        """Get selected codec from button group"""
        group = self.codec_groups.get(group_name)
        if group:
            selected = group.checkedButton()
            if selected:
                return selected.property("value")
        return None
    
    def load_settings(self, qsettings: QSettings):
        """Load settings from QSettings"""
        # Load values if they exist
        if qsettings.value("crf"):
            self.controls['crf'].setValue(int(qsettings.value("crf", DEFAULT_CRF)))
        if qsettings.value("abr"):
            self.controls['abr'].setValue(int(qsettings.value("abr", DEFAULT_ABR)))
    
    def save_settings(self, qsettings: QSettings):
        """Save current settings to QSettings"""
        settings = self.get_current_settings()
        for key, value in settings.items():
            qsettings.setValue(key, value)