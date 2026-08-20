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
                              QTabWidget, QSpinBox, QComboBox, QGridLayout, QFrame,
                              QSizePolicy, QMessageBox)
from PySide6.QtCore import Qt, Signal, QSettings, QTimer, QSize
from PySide6.QtGui import QAction, QIcon, QDragEnterEvent, QDropEvent, QFont, QColor, QBrush

from config import (VIDEO_CODECS, AUDIO_CODECS, OUTPUT_FORMATS, VIDEO_EXTENSIONS,
                   ENCODING_PRESETS, PAR_PRESETS, DAR_PRESETS, DEINTERLACERS,
                   RESOLUTION_PRESETS, SCALE_ALGORITHMS, DEFAULT_SCALE_ALGORITHM,
                   DEFAULT_CRF, DEFAULT_ABR, MAX_THREADS, DEFAULT_SETTINGS,
                   QUALITY_PRESETS, APP_NAME, APP_VERSION)
from modules.process_manager import format_duration, format_size


class FileListWidget(QListWidget):
    """Custom list widget with drag and drop support"""
    
    files_dropped = Signal(list)
    
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(False)
        self.setSpacing(2)
        self._setup_style()
    
    def _setup_style(self):
        # Note: no ::item box rules here — a styled ::item makes Qt ignore the
        # per-item background brush used for running/success/failed colouring.
        self.setStyleSheet("""
            QListWidget {
                border: 2px solid #aaa;
                border-radius: 5px;
                padding: 5px;
                background-color: #f9f9f9;
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
    pause_clicked = Signal()   # toggles pause/resume (main window decides which)
    files_added = Signal(list)
    files_removed = Signal(list)
    queue_cleared = Signal()
    files_reordered = Signal(int, int)   # from_index, to_index
    
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
        
        # Built-in quality presets
        pm = self.main_window.preset_manager
        for key, preset in QUALITY_PRESETS.items():
            self._add_action(presets_menu, preset['name'], None,
                             lambda checked=False, k=key: pm.apply_preset(k))

        presets_menu.addSeparator()
        self._add_action(presets_menu, 'Import Preset…', None, pm.import_preset)
        self._add_action(presets_menu, 'Export Preset…', None, pm.export_preset)
        self._add_action(presets_menu, 'Delete Preset…', None, pm.delete_preset)

        presets_menu.addSeparator()
        self._add_action(presets_menu, 'Save Current as Defaults', None, pm.save_as_defaults)
        self._add_action(presets_menu, 'Reset to Factory Defaults', None, pm.reset_defaults)

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
        self.file_list.model().rowsMoved.connect(self._on_rows_moved)
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
        layout.addWidget(self._create_progress_group())

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
        
        self.controls['pause'] = QPushButton("Pause")
        self.controls['pause'].clicked.connect(self.pause_clicked)
        self.controls['pause'].setEnabled(False)
        self.controls['pause'].setStyleSheet("""
            QPushButton {
                font-size: 14px;
                font-weight: bold;
                padding: 8px;
                background-color: #ffc107;
                color: #333;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #e0a800;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #666;
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
        buttons_layout.addWidget(self.controls['pause'])
        buttons_layout.addWidget(self.controls['stop'])
        
        layout.addLayout(buttons_layout)
        layout.addStretch()
        
        return panel
    
    # ------------------------------------------------------------------
    # Progress panel
    # ------------------------------------------------------------------
    _BAR_STYLE = """
        QProgressBar {
            border: 1px solid #c8c8c8; border-radius: 4px; background: #f0f0f0;
            text-align: center; height: 18px; font-weight: bold; color: #333;
        }
        QProgressBar::chunk { background-color: %s; border-radius: 3px; }
    """

    def _make_stat_tile(self, title: str) -> QLabel:
        """Small 'label over value' tile used in the stats row"""
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet("QFrame { background: #fafafa; border: 1px solid #e0e0e0; border-radius: 4px; }")
        box = QVBoxLayout(frame)
        box.setContentsMargins(8, 4, 8, 4)
        box.setSpacing(0)
        caption = QLabel(title)
        caption.setStyleSheet("color: #888; font-size: 10px; border: none;")
        value = QLabel("--")
        value.setStyleSheet("font-weight: bold; font-size: 13px; border: none;")
        box.addWidget(caption)
        box.addWidget(value)
        value.tile = frame
        return value

    def _create_progress_group(self) -> QGroupBox:
        group = QGroupBox("Progress")
        layout = QVBoxLayout()
        layout.setSpacing(6)

        # Header: phase pill + file name + counter
        header = QHBoxLayout()
        self.phase_label = QLabel("Idle")
        self.phase_label.setStyleSheet(
            "QLabel { background: #e0e0e0; color: #444; border-radius: 8px; padding: 1px 8px; font-size: 11px; font-weight: bold; }")
        self.current_file_label = QLabel("Ready — add files and press Start")
        self.current_file_label.setStyleSheet("font-weight: bold;")
        self.current_file_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.counter_label = QLabel("")
        self.counter_label.setStyleSheet("color: #666;")
        header.addWidget(self.phase_label)
        header.addWidget(self.current_file_label, 1)
        header.addWidget(self.counter_label)
        layout.addLayout(header)

        # Current file bar + ETA line
        self.file_progress_bar = QProgressBar()
        self.file_progress_bar.setRange(0, 100)
        self.file_progress_bar.setFormat("%p%")
        self.file_progress_bar.setStyleSheet(self._BAR_STYLE % "#4a90d9")
        layout.addWidget(self.file_progress_bar)

        file_line = QHBoxLayout()
        self.file_eta_label = QLabel("File ETA: --")
        self.file_elapsed_label = QLabel("Elapsed: --")
        self.file_elapsed_label.setStyleSheet("color: #666;")
        file_line.addWidget(self.file_eta_label)
        file_line.addStretch()
        file_line.addWidget(self.file_elapsed_label)
        layout.addLayout(file_line)

        # Overall bar + ETA line
        overall_caption = QLabel("Overall")
        overall_caption.setStyleSheet("color: #666; font-size: 11px; margin-top: 4px;")
        layout.addWidget(overall_caption)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setStyleSheet(self._BAR_STYLE % "#28a745")
        layout.addWidget(self.progress_bar)

        total_line = QHBoxLayout()
        self.time_label = QLabel("Total ETA: --")
        self.total_elapsed_label = QLabel("Elapsed: --")
        self.total_elapsed_label.setStyleSheet("color: #666;")
        total_line.addWidget(self.time_label)
        total_line.addStretch()
        total_line.addWidget(self.total_elapsed_label)
        layout.addLayout(total_line)

        # Stats tiles
        tiles = QHBoxLayout()
        tiles.setSpacing(6)
        self.stat_fps = self._make_stat_tile("FPS")
        self.stat_speed = self._make_stat_tile("Speed")
        self.stat_bitrate = self._make_stat_tile("Bitrate")
        self.stat_size = self._make_stat_tile("Output size")
        self.stat_position = self._make_stat_tile("Position")
        for tile in (self.stat_fps, self.stat_speed, self.stat_bitrate, self.stat_size, self.stat_position):
            tiles.addWidget(tile.tile, 1)
        layout.addLayout(tiles)

        # Compact status line (kept for messages such as errors / stopped)
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #555; font-size: 11px;")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.status_label)

        group.setLayout(layout)
        return group

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

        # Resolution settings
        layout.addWidget(self._create_resolution_group())

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
    
    def _create_resolution_group(self) -> QGroupBox:
        """Create the optional output-resolution controls"""
        group = QGroupBox("Output Resolution")
        grid = QGridLayout()

        grid.addWidget(QLabel("Resolution:"), 0, 0)
        self.controls['resolution_mode'] = QComboBox()
        self.controls['resolution_mode'].addItems(list(RESOLUTION_PRESETS.keys()))
        self.controls['resolution_mode'].setToolTip(
            "Scale the output video. Presets keep the aspect ratio and pick the width\n"
            "automatically (even number). Choose Custom to enter exact dimensions."
        )
        self.controls['resolution_mode'].currentTextChanged.connect(self._on_resolution_mode_changed)
        grid.addWidget(self.controls['resolution_mode'], 0, 1)

        grid.addWidget(QLabel("Custom Size:"), 1, 0)
        size_widget = QWidget()
        size_layout = QHBoxLayout(size_widget)
        size_layout.setContentsMargins(0, 0, 0, 0)

        self.controls['custom_width'] = QSpinBox()
        self.controls['custom_width'].setRange(0, 16384)
        self.controls['custom_width'].setSingleStep(2)
        self.controls['custom_width'].setSpecialValueText("auto")
        self.controls['custom_width'].setSuffix(" px")
        self.controls['custom_width'].setToolTip("Width in pixels (auto = keep aspect ratio)")

        self.controls['custom_height'] = QSpinBox()
        self.controls['custom_height'].setRange(0, 16384)
        self.controls['custom_height'].setSingleStep(2)
        self.controls['custom_height'].setSpecialValueText("auto")
        self.controls['custom_height'].setSuffix(" px")
        self.controls['custom_height'].setToolTip("Height in pixels (auto = keep aspect ratio)")

        size_layout.addWidget(self.controls['custom_width'])
        size_layout.addWidget(QLabel("×"))
        size_layout.addWidget(self.controls['custom_height'])
        grid.addWidget(size_widget, 1, 1)

        grid.addWidget(QLabel("Algorithm:"), 2, 0)
        self.controls['scale_algorithm'] = QComboBox()
        self.controls['scale_algorithm'].addItems(SCALE_ALGORITHMS)
        self.controls['scale_algorithm'].setCurrentText(DEFAULT_SCALE_ALGORITHM)
        self.controls['scale_algorithm'].setToolTip(
            "lanczos: sharpest, best for downscaling (default)\n"
            "bicubic / spline: smooth, good general purpose\n"
            "bilinear: fastest, softer\n"
            "neighbor: pixel-exact, for pixel art / integer scaling"
        )
        grid.addWidget(self.controls['scale_algorithm'], 2, 1)

        self.controls['no_upscale'] = QCheckBox("Never upscale (only shrink larger videos)")
        self.controls['no_upscale'].setChecked(True)
        self.controls['no_upscale'].setToolTip(
            "When enabled, videos already smaller than the target keep their original size."
        )
        grid.addWidget(self.controls['no_upscale'], 3, 0, 1, 2)

        group.setLayout(grid)
        self._on_resolution_mode_changed(self.controls['resolution_mode'].currentText())
        return group

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

        deint_row = QHBoxLayout()
        deint_row.addWidget(QLabel("Deinterlacer:"))
        self.controls['deinterlacer'] = QComboBox()
        for text, value in DEINTERLACERS:
            self.controls['deinterlacer'].addItem(text, value)
        self.controls['deinterlacer'].setToolTip(
            "QTGMC: highest quality, needs AviSynth+ (Windows).\n"
            "bwdif / yadif: built into FFmpeg, work on every OS and with any input."
        )
        self.controls['deinterlacer'].currentIndexChanged.connect(self._on_deinterlacer_changed)
        deint_row.addWidget(self.controls['deinterlacer'], 1)

        deint_layout.addWidget(self.controls['deinterlace'])
        deint_layout.addLayout(deint_row)
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

        # Auto-link: deinterlace requires AviSynth+, disabling AviSynth+ disables deinterlace
        self.controls['deinterlace'].toggled.connect(self._on_deinterlace_toggled)
        self.controls['use_avisynth'].toggled.connect(self._on_avisynth_toggled)

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
        
        self.controls['corrupt_fix'] = QCheckBox("Tolerate corrupt streams (regenerate timestamps, drop bad packets)")
        self.controls['corrupt_fix'].setToolTip("Adds -fflags +genpts+discardcorrupt for damaged TS/DVB captures.")
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
        self.controls['replace_files'].setToolTip(
            "Move the encoded output over the original filename.\n"
            "The original is kept next to it as <name>.old<ext> unless\n"
            "'Delete Source Files' is also enabled."
        )
        file_layout.addWidget(self.controls['replace_files'])

        self.controls['delete_source'] = QCheckBox("Delete Source Files After Processing")
        self.controls['delete_source'].setStyleSheet("color: #d9534f; font-weight: bold;")
        self.controls['delete_source'].setToolTip(
            "Permanently delete each source file right after its encode succeeds,\n"
            "one by one as the queue progresses, to free disk space.\n"
            "Only happens when the output exists and is non-empty; failed or\n"
            "stopped files are never deleted. Combined with 'Replace Original\n"
            "Files', the .old backup is removed as well. There is no undo."
        )
        file_layout.addWidget(self.controls['delete_source'])
        
        file_group.setLayout(file_layout)
        layout.addWidget(file_group)

        # Quality analysis
        quality_group = QGroupBox("Quality Analysis")
        quality_layout = QVBoxLayout()

        self.controls['calculate_vmaf'] = QCheckBox("Calculate VMAF Score After Encoding")
        self.controls['calculate_vmaf'].setToolTip(
            "Run Netflix VMAF (Video Multi-Method Assessment Fusion) after encoding.\n"
            "Compares the encoded output against the original to produce a 0-100 quality score.\n"
            "Requires libvmaf support in FFmpeg. Skipped when video codec is 'copy'."
        )
        quality_layout.addWidget(self.controls['calculate_vmaf'])

        quality_group.setLayout(quality_layout)
        layout.addWidget(quality_group)

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
    
    def _on_resolution_mode_changed(self, text):
        """Enable scaling controls only when scaling is requested"""
        scaling = RESOLUTION_PRESETS.get(text) is not None
        is_custom = text == "Custom"
        self.controls['custom_width'].setEnabled(is_custom)
        self.controls['custom_height'].setEnabled(is_custom)
        self.controls['scale_algorithm'].setEnabled(scaling)
        self.controls['no_upscale'].setEnabled(scaling)

    def _on_dar_mode_changed(self, text):
        """Handle DAR mode change"""
        self.controls['dar_custom'].setEnabled(text == "Custom")

    def _uses_qtgmc(self) -> bool:
        return self.controls['deinterlacer'].currentData() == 'qtgmc'

    def _on_deinterlace_toggled(self, checked):
        """QTGMC lives in the AviSynth script, so enable AviSynth+ when it is chosen"""
        if checked and self._uses_qtgmc():
            self.controls['use_avisynth'].setChecked(True)

    def _on_deinterlacer_changed(self, _index):
        if self.controls['deinterlace'].isChecked() and self._uses_qtgmc():
            self.controls['use_avisynth'].setChecked(True)

    def _on_avisynth_toggled(self, checked):
        """Turning AviSynth+ off makes QTGMC unavailable — fall back to bwdif"""
        if not checked and self.controls['deinterlace'].isChecked() and self._uses_qtgmc():
            index = self.controls['deinterlacer'].findData('bwdif')
            if index >= 0:
                self.controls['deinterlacer'].setCurrentIndex(index)

    def _on_rows_moved(self, _parent, start, end, _dest, row):
        """Internal drag-and-drop in the list → reorder the real queue"""
        if start != end:
            return  # multi-row moves are not supported by the queue model
        to_index = row - 1 if row > start else row
        # Defer: the view is still inside its drop handling when rowsMoved fires
        QTimer.singleShot(0, lambda: self.files_reordered.emit(start, to_index))

    def _on_add_files(self):
        """Add files dialog"""
        patterns = ' '.join(f'*{ext}' for ext in VIDEO_EXTENSIONS)
        files, _ = QFileDialog.getOpenFileNames(
            self.main_window,
            "Select Input Files",
            "",
            f"Video Files ({patterns});;All Files (*.*)"
        )
        if files:
            self.files_added.emit(files)
    
    def _on_add_folder(self):
        """Add folder dialog"""
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
        QMessageBox.about(
            self.main_window,
            f"About {APP_NAME}",
            f"{APP_NAME} v{APP_VERSION}\n\n"
            "Professional video processing with:\n"
            "• Multi-format support\n"
            "• Hardware acceleration\n"
            "• AviSynth+ integration\n"
            "• PAR/DAR support\n"
            "• Optional resolution scaling\n"
            "• QTGMC / bwdif / yadif deinterlacing\n"
            "• Batch processing\n\n"
            "Drag and drop files or folders to process."
        )
    
    def update_file_list(self, files):
        """Rebuild the list from the queue, keeping per-file state colours"""
        self._files = list(files)
        self.file_list.clear()
        for file in files:
            label = f"{file.filename} ({file.get_file_size_mb():.1f} MB)"
            if file.vmaf_score is not None:
                label += f" | VMAF: {file.vmaf_score:.1f}"
            item = QListWidgetItem(label)
            item.setToolTip(file.filepath)
            item.setSizeHint(QSize(0, 32))
            self._style_item(item, getattr(file, 'status', 'pending'))
            self.file_list.addItem(item)

    def update_file_count(self, count):
        """Update file count label"""
        self.file_count_label.setText(f"{count} files in queue")
        self.controls['start'].setEnabled(count > 0)
    
    _FILE_STATE_COLORS = {
        'running': QColor(255, 244, 179),   # soft yellow
        'success': QColor(200, 240, 200),   # soft green
        'failed': QColor(250, 200, 200),    # soft red
    }
    _FILE_STATE_GLYPHS = {
        'running': '▶  ',
        'success': '✔  ',
        'failed': '✖  ',
    }

    def update_progress(self, value, maximum):
        """Update overall progress bar"""
        self.progress_bar.setMaximum(maximum)
        self.progress_bar.setValue(value)

    def update_status(self, message):
        """Update the compact status line (full text in tooltip)"""
        available_width = self.status_label.width() - 10
        shown = message
        if available_width > 0:
            shown = self.status_label.fontMetrics().elidedText(
                message, Qt.TextElideMode.ElideRight, available_width)
        self.status_label.setText(shown)
        self.status_label.setToolTip(message)

    def update_stats(self, snap: Dict[str, Any]):
        """Refresh the progress panel from a ProcessManager snapshot"""
        phase = snap.get('phase') or 'Working'
        if not getattr(self, '_ui_paused', False):
            self.phase_label.setText(phase)
            self.phase_label.setStyleSheet(
                "QLabel { background: #d6e9ff; color: #1b4f8a; border-radius: 8px; padding: 1px 8px; font-size: 11px; font-weight: bold; }")

        name = snap.get('file_name') or ''
        width = max(50, self.current_file_label.width() - 10)
        self.current_file_label.setText(
            self.current_file_label.fontMetrics().elidedText(name, Qt.TextElideMode.ElideMiddle, width))
        self.current_file_label.setToolTip(name)
        self.counter_label.setText(f"{snap.get('file_index', 0) + 1} / {snap.get('total_files', 0)}")

        percent = snap.get('percent')
        if percent is None:
            self.file_progress_bar.setRange(0, 0)   # busy indicator (unknown duration)
        else:
            self.file_progress_bar.setRange(0, 100)
            self.file_progress_bar.setValue(int(percent))

        self.file_eta_label.setText(f"File ETA: {format_duration(snap.get('eta_file'))}")
        self.file_elapsed_label.setText(f"Elapsed: {format_duration(snap.get('elapsed_file'))}")
        self.time_label.setText(f"Total ETA: {format_duration(snap.get('eta_total'))}")
        self.total_elapsed_label.setText(f"Elapsed: {format_duration(snap.get('elapsed_total'))}")

        fps = snap.get('fps')
        speed = snap.get('speed')
        self.stat_fps.setText(f"{fps:.0f}" if fps else "--")
        self.stat_speed.setText(f"{speed:.2f}×" if speed else "--")
        self.stat_bitrate.setText(snap.get('bitrate') or "--")
        self.stat_size.setText(format_size(snap.get('size')))
        out_time, duration = snap.get('out_time'), snap.get('duration')
        if out_time is not None:
            pos = format_duration(out_time)
            self.stat_position.setText(f"{pos} / {format_duration(duration)}" if duration else pos)
        else:
            self.stat_position.setText("--")

    def reset_progress_panel(self, message: str = "Ready — add files and press Start"):
        """Return the panel to its idle look"""
        self.phase_label.setText("Idle")
        self.phase_label.setStyleSheet(
            "QLabel { background: #e0e0e0; color: #444; border-radius: 8px; padding: 1px 8px; font-size: 11px; font-weight: bold; }")
        self.current_file_label.setText(message)
        self.current_file_label.setToolTip("")
        self.counter_label.setText("")
        self.file_progress_bar.setRange(0, 100)
        self.file_progress_bar.setValue(0)
        self.file_eta_label.setText("File ETA: --")
        self.file_elapsed_label.setText("Elapsed: --")
        self.time_label.setText("Total ETA: --")
        self.total_elapsed_label.setText("Elapsed: --")
        for tile in (self.stat_fps, self.stat_speed, self.stat_bitrate, self.stat_size, self.stat_position):
            tile.setText("--")

    def set_file_state(self, index: int, state: str):
        """Colour and mark a queue entry by processing state (and remember it on the file)"""
        files = getattr(self, '_files', [])
        if 0 <= index < len(files):
            files[index].status = state
        item = self.file_list.item(index)
        if item:
            self._style_item(item, state)
            if state == 'running':
                self.file_list.clearSelection()
                self.file_list.scrollToItem(item)

    def _style_item(self, item: 'QListWidgetItem', state: str):
        """Apply background colour and a leading glyph for the given state"""
        color = self._FILE_STATE_COLORS.get(state)
        if color:
            item.setBackground(QBrush(color))
        glyph = self._FILE_STATE_GLYPHS.get(state, '')
        text = item.text()
        for g in self._FILE_STATE_GLYPHS.values():
            if text.startswith(g):
                text = text[len(g):]
                break
        item.setText(glyph + text)

    def set_file_vmaf(self, index: int, score: float):
        item = self.file_list.item(index)
        if item:
            item.setText(f"{item.text()} | VMAF: {score:.1f}")

    def update_ffmpeg_status(self, available):
        """Update FFmpeg status in status bar"""
        if available:
            self.ffmpeg_status.setText("FFmpeg: ✓ Found")
            self.ffmpeg_status.setStyleSheet("color: green;")
        else:
            self.ffmpeg_status.setText("FFmpeg: ✗ Not Found")
            self.ffmpeg_status.setStyleSheet("color: red;")
    
    def set_paused_state(self, paused: bool):
        """Reflect pause/resume in the controls and progress panel"""
        self._ui_paused = paused
        self.controls['pause'].setText("Resume" if paused else "Pause")
        if paused:
            self.phase_label.setText("Paused")
            self.phase_label.setStyleSheet(
                "QLabel { background: #fff3cd; color: #856404; border-radius: 8px; padding: 1px 8px; font-size: 11px; font-weight: bold; }")
        # on resume the next progress snapshot restores the phase pill

    def set_processing_state(self, is_processing):
        """Set UI state for processing"""
        self._processing_active = is_processing
        self._ui_paused = False
        if is_processing:
            self.reset_progress_panel("Starting…")
        else:
            self.phase_label.setText("Idle")
            self.phase_label.setStyleSheet(
                "QLabel { background: #e0e0e0; color: #444; border-radius: 8px; padding: 1px 8px; font-size: 11px; font-weight: bold; }")
            self.file_progress_bar.setRange(0, 100)
            self.file_eta_label.setText("File ETA: --")
            self.time_label.setText("Total ETA: --")
        self.controls['start'].setEnabled(not is_processing)
        self.controls['pause'].setEnabled(is_processing)
        self.controls['pause'].setText("Pause")
        self.controls['stop'].setEnabled(is_processing)
        self.controls['add_files'].setEnabled(True)        # always enabled
        self.controls['add_folder'].setEnabled(True)       # always enabled
        self.controls['remove_files'].setEnabled(True)     # always enabled
        self.controls['clear_files'].setEnabled(not is_processing)  # disable during processing
        self.tabs.setEnabled(not is_processing)             # settings stay locked

        # Disable internal drag-drop reordering during processing (prevents index corruption)
        # but keep external file drops working
        if is_processing:
            self.file_list.setDragDropMode(QListWidget.DragDropMode.NoDragDrop)
            self.file_list.setAcceptDrops(True)
        else:
            self.file_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
            self.file_list.setAcceptDrops(True)
    
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
            'deinterlacer': self.controls['deinterlacer'].currentData(),
            'tff': self.controls['tff'].isChecked(),
            'reduce_fps': self.controls['reduce_fps'].isChecked(),
            'use_avisynth': self.controls['use_avisynth'].isChecked(),
            'use_ffms2': self.controls['use_ffms2'].isChecked(),
            'transcode_video': self.controls['transcode_video'].isChecked(),
            'transcode_audio': self.controls['transcode_audio'].isChecked(),
            'corrupt_fix': self.controls['corrupt_fix'].isChecked(),
            'replace_files': self.controls['replace_files'].isChecked(),
            'delete_source': self.controls['delete_source'].isChecked(),
            'threads': self.controls['threads'].value(),
            'ffmpeg_extras': self.controls['ffmpeg_extras'].text(),
            'avisynth_extras': self.controls['avisynth_extras'].toPlainText(),
            'par_mode': self.controls['par_mode'].currentText(),
            'par_custom': self.controls['par_custom'].text(),
            'dar_mode': self.controls['dar_mode'].currentText(),
            'dar_custom': self.controls['dar_custom'].text(),
            'resolution_mode': self.controls['resolution_mode'].currentText(),
            'custom_width': self.controls['custom_width'].value(),
            'custom_height': self.controls['custom_height'].value(),
            'no_upscale': self.controls['no_upscale'].isChecked(),
            'scale_algorithm': self.controls['scale_algorithm'].currentText(),
            'calculate_vmaf': self.controls['calculate_vmaf'].isChecked()
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
        """Load all settings from QSettings"""
        int_keys = ('crf', 'abr', 'threads', 'custom_width', 'custom_height')
        settings = {}
        for key in qsettings.allKeys():
            value = qsettings.value(key)
            if value is None:
                continue
            # QSettings stores booleans as strings on some platforms
            if isinstance(value, str) and value.lower() in ('true', 'false'):
                value = value.lower() == 'true'
            elif key in int_keys:
                value = int(value)
            settings[key] = value
        if settings:
            self.main_window.preset_manager.apply_settings(settings)
    
    def save_settings(self, qsettings: QSettings):
        """Save current settings to QSettings"""
        settings = self.get_current_settings()
        for key, value in settings.items():
            qsettings.setValue(key, value)