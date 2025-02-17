import sys
import os
import re
import time
import logging
import subprocess
import multiprocessing
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QCheckBox, QRadioButton,
                             QPushButton, QLineEdit, QSlider, QTextEdit,
                             QFileDialog, QButtonGroup, QScrollArea, QGroupBox,
                             QListWidget, QStyle, QProgressBar, QSplitter,
                             QMenuBar, QMenu, QAction, QMessageBox, QListWidgetItem)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, pyqtSlot, QSize
from PyQt5.QtGui import QIcon, QDrag, QDragEnterEvent, QDropEvent


def multiple_replace(string, rep_dict):
    pattern = re.compile("|".join([re.escape(k) for k in sorted(rep_dict, key=len, reverse=True)]), flags=re.DOTALL)
    return pattern.sub(lambda x: rep_dict[x.group(0)], string)


def get_logger(filename):
    logFormatter = logging.Formatter("%(asctime)s [%(threadName)-12.12s] [%(levelname)-5.5s]  %(message)s")
    rootLogger = logging.getLogger()
    rootLogger.propagate = False
    rootLogger.setLevel(logging.INFO)
    fileHandler = logging.FileHandler(f"{filename}.log")
    fileHandler.setFormatter(logFormatter)
    rootLogger.addHandler(fileHandler)
    consoleHandler = logging.StreamHandler()
    consoleHandler.setFormatter(logFormatter)
    rootLogger.addHandler(consoleHandler)
    return rootLogger


class File:
    def __init__(self, file):
        self.number = file[0]
        self.filename = file[1]
        self.orig_name = self.filename
        self.basename = os.path.splitext(self.filename)[0]
        self.extension = ".mkv"
        self.transcodename = f"{self.basename}.trans.avi"
        self.errorname = f"{self.filename}.error"
        self.ffindex = f"{self.filename}.ffindex"
        self.tempffindex = f"{self.transcodename}.ffindex"
        self.displayname = self.filename.split('/')[-1]
        self.dir = os.path.dirname(os.path.realpath(self.filename))
        self.ffmpeg_errors = []
        self.avsfile = f"{self.basename}.avs"

    def create_logger(self):
        self.log = get_logger(self.filename)


class FileListWidget(QListWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.InternalMove)
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setStyleSheet("""
            QListWidget {
                border: 2px solid #aaa;
                border-radius: 5px;
                padding: 5px;
            }
            QListWidget::item {
                border-bottom: 1px solid #eee;
                padding: 5px;
            }
            QListWidget::item:selected {
                background-color: #0078D7;
                color: white;
            }
            QListWidget::item:alternate {
                background-color: #f7f7f7;
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
            self.main_window.add_files(links)
        else:
            super().dropEvent(event)


class ProcessThread(QThread):
    progress_signal = pyqtSignal(str, int)  # message, progress percentage
    info_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()
    file_started_signal = pyqtSignal(int)  # file index
    file_finished_signal = pyqtSignal(int, bool)  # file index, success

    def __init__(self, file_queue, app_window):
        super().__init__()
        self.file_queue = file_queue
        self.app_window = app_window
        self.should_stop = False
        self.process = None

    def run(self):
        self.queue(self.file_queue)
        self.finished_signal.emit()

    def queue(self, files):
        for i, file in enumerate(files):
            if self.should_stop:
                return

            fileobj = File((i, file))
            fileobj.create_logger()

            # Set output name based on current settings
            fileobj.outputname = f"{fileobj.basename}_{self.app_window.crf_slider.value()}" \
                                 f"{self.app_window.get_selected_codec(self.app_window.video_codec_group)}_" \
                                 f"{self.app_window.get_selected_codec(self.app_window.audio_codec_group)}" \
                                 f"{self.app_window.abr_slider.value()}{fileobj.extension}"

            self.file_started_signal.emit(i)
            self.info_signal.emit(f"Processing {i + 1}/{len(files)}: {fileobj.displayname}")

            should_transcode_video = self.app_window.transcode_video_check.isChecked()
            should_transcode_audio = self.app_window.transcode_audio_check.isChecked()

            # Handle transcoding if needed
            if should_transcode_video or should_transcode_audio:
                if not self.transcode(fileobj, should_transcode_video, should_transcode_audio):
                    self.file_finished_signal.emit(i, False)
                    continue
                fileobj.filename = fileobj.transcodename

            # Assemble and execute final FFmpeg command
            command = self.assemble_final(fileobj)
            return_code = self.open_process(command, fileobj)

            success = return_code == 0 and not self.should_stop
            self.file_finished_signal.emit(i, success)

            # Handle file replacement if requested
            if success and self.app_window.replace_check.isChecked():
                self.replace_file(fileobj.outputname, fileobj.orig_name, fileobj.log)

            # Cleanup temporary files
            self.cleanup_temp_files(fileobj)

            if fileobj.ffmpeg_errors:
                self.info_signal.emit("Errors:\n" + '\n'.join(fileobj.ffmpeg_errors))

    def transcode(self, fileobj, transcode_video, transcode_audio):
        fileobj.log.info("Transcode process started")
        preset = self.app_window.get_preset(self.app_window.speed_slider.value())

        command = ['ffmpeg.exe -err_detect crccheck+bitstream+buffer -hide_banner']
        command.append(f'-i "{fileobj.filename}"')
        command.append(f'-preset {preset}')
        command.append('-map 0:v -map 0:a? -map 0:s?')

        if transcode_video and transcode_audio:
            command.append('-c:a pcm_s32le -c:v rawvideo')
        elif transcode_video:
            command.append('-c:a copy -c:v rawvideo')
        elif transcode_audio:
            command.append('-c:a pcm_s32le -c:v copy')

        command.append('-c:s copy')
        command.append(f'"{fileobj.transcodename}" -y')

        return self.open_process(" ".join(command), fileobj) == 0

    def assemble_final(self, fileobj):
        command = ['ffmpeg.exe -err_detect crccheck+bitstream+buffer -hide_banner']

        video_codec = self.app_window.get_selected_codec(self.app_window.video_codec_group)
        if video_codec in ["hevc_nvenc", "h264_nvenc"]:
            command.append("-hwaccel cuda")

        if self.app_window.use_avisynth_check.isChecked():
            command.append(f'-i "{fileobj.avsfile}" -y')
            self.create_avs(fileobj)
        else:
            command.append(f'-i "{fileobj.filename}" -y')

        preset = self.app_window.get_preset(self.app_window.speed_slider.value())
        command.append(f'-preset {preset}')
        command.append('-map 0:v -map 0:a? -map 0:s?')

        if self.app_window.stereo_check.isChecked():
            command.append('-ac 2')

        # Video codec settings
        if video_codec == "copy":
            command.append('-c:v copy')
        else:
            command.append(f'-c:v {video_codec}')
            if video_codec in ["hevc_nvenc", "h264_nvenc"]:
                command.append(f'-cq {self.app_window.crf_slider.value()}')
            else:
                command.append(f'-crf {self.app_window.crf_slider.value()}')

        # Audio codec settings
        audio_codec = self.app_window.get_selected_codec(self.app_window.audio_codec_group)
        if audio_codec == "copy":
            command.append('-c:a copy')
        else:
            command.append(f'-c:a {audio_codec}')
            command.append(f'-b:a {self.app_window.abr_slider.value()}k')

        command.append('-c:s copy')

        if self.app_window.corrupt_check.isChecked():
            command.append("-bsf:v h264_mp4toannexb")

        # Copy metadata from source
        command.append('-map_metadata 0')
        command.append('-map_chapters 0')

        # Add extras and metadata
        if self.app_window.ffmpeg_extras_edit.text().strip():
            command.append(self.app_window.ffmpeg_extras_edit.text())

        command.append('-metadata comment="Made with Video Processor"')
        command.append('-movflags +faststart')
        command.append('-bf 2')
        command.append('-flags +cgop')
        command.append('-pix_fmt yuv420p')
        command.append(f'-f matroska "{fileobj.outputname}"')
        command.append('-y')

        return " ".join(command)

    def create_avs(self, fileobj):
        with open(fileobj.avsfile, "w") as avsfile:
            plugins_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")

            # Load plugins
            avsfile.write(f'LoadPlugin("{plugins_path}/masktools2.dll")\n')
            avsfile.write(f'LoadPlugin("{plugins_path}/mvtools2.dll")\n')
            avsfile.write(f'LoadPlugin("{plugins_path}/nnedi3.dll")\n')
            avsfile.write(f'LoadPlugin("{plugins_path}/ffms2.dll")\n')
            avsfile.write(f'LoadPlugin("{plugins_path}/RgTools.dll")\n')

            # Import scripts
            avsfile.write(f'Import("{plugins_path}/QTGMC.avsi")\n')
            avsfile.write(f'Import("{plugins_path}/Zs_RF_Shared.avsi")\n')

            # Source
            if self.app_window.use_ffms2_check.isChecked():
                avsfile.write(f'FFVideoSource("{fileobj.filename}", track=-1)\n')
            else:
                avsfile.write(f'AVISource("{fileobj.filename}", audio=true)\n')

            avsfile.write('SetFilterMTMode("FFVideoSource", 3)\n')
            avsfile.write('ConvertToYV24(matrix="rec709")\n')
            avsfile.write(f'Prefetch({multiprocessing.cpu_count()})\n')

            # Add any custom AviSynth extras
            if self.app_window.avisynth_extras_edit.toPlainText().strip():
                avsfile.write(self.app_window.avisynth_extras_edit.toPlainText() + '\n')

            if self.app_window.tff_check.isChecked():
                avsfile.write('AssumeTFF()\n')

            if self.app_window.deinterlace_check.isChecked():
                preset = self.app_window.get_preset(self.app_window.speed_slider.value())
                if self.app_window.reduce_fps_check.isChecked():
                    avsfile.write(f'QTGMC(Preset="{preset}", FPSDivisor=2, EdiThreads={multiprocessing.cpu_count()})\n')
                else:
                    avsfile.write(f'QTGMC(Preset="{preset}", EdiThreads={multiprocessing.cpu_count()})\n')

    def replace_file(self, rename_from, rename_to, log):
        log.info(f"Replacing {rename_to} with {rename_from}")
        if os.path.exists(rename_from):
            _, extension = os.path.splitext(rename_to)
            old_file_name = f"{rename_to}.old{extension}"
            os.rename(rename_to, old_file_name)
            os.rename(rename_from, rename_to)

    def cleanup_temp_files(self, fileobj):
        if os.path.exists(fileobj.transcodename):
            os.remove(fileobj.transcodename)
        if os.path.exists(fileobj.ffindex):
            os.remove(fileobj.ffindex)
        if os.path.exists(fileobj.tempffindex):
            os.remove(fileobj.tempffindex)
        if os.path.exists(fileobj.avsfile):
            os.remove(fileobj.avsfile)

    def open_process(self, command_line, fileobj):
        fileobj.log.info(f"Executing command {command_line}")

        with subprocess.Popen(command_line,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT,
                              universal_newlines=True,
                              encoding="utf8") as self.process:

            errors = ["error", "invalid"]
            for line in self.process.stdout:
                fileobj.log.info(line.strip())
                self.progress_signal.emit(multiple_replace(line,
                                                           {"       ": " ",
                                                            "    ": " ",
                                                            "time=": "",
                                                            "bitrate=  ": "br:",
                                                            "speed": "rate",
                                                            "size=": "",
                                                            "frame": "f",
                                                            "=": ":",
                                                            "\n": ""}), 0)
                for error in errors:
                    if error in line.lower():
                        fileobj.ffmpeg_errors.append(line.strip())

        return_code = self.process.wait()
        fileobj.log.info(f"Return code: {return_code}")
        return return_code


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Processor")
        self.file_queue = []
        self.process_thread = None
        self.setAcceptDrops(True)

        # Set window icon
        self.setWindowIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))

        # Initialize UI
        self.create_menu_bar()
        self.initUI()

        # Set minimum window size
        self.setMinimumWidth(1000)
        self.setMinimumHeight(800)

    def create_menu_bar(self):
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu('File')

        add_files_action = QAction('Add Files', self)
        add_files_action.setShortcut('Ctrl+O')
        add_files_action.triggered.connect(self.select_files)
        file_menu.addAction(add_files_action)

        clear_queue_action = QAction('Clear Queue', self)
        clear_queue_action.triggered.connect(self.clear_queue)
        file_menu.addAction(clear_queue_action)

        file_menu.addSeparator()

        exit_action = QAction('Exit', self)
        exit_action.setShortcut('Ctrl+Q')
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Help menu
        help_menu = menubar.addMenu('Help')

        about_action = QAction('About', self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def initUI(self):
        # Create main widget and layout
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # Create splitter for resizable sections
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # Left panel - File list and controls
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        # File list section
        files_group = QGroupBox("Input Files")
        files_layout = QVBoxLayout()

        # Add file list widget
        self.file_list = FileListWidget(main_window=self)
        files_layout.addWidget(self.file_list)

        # File controls
        file_controls = QHBoxLayout()
        self.add_files_btn = QPushButton("Add Files")
        self.add_files_btn.clicked.connect(self.select_files)
        self.remove_files_btn = QPushButton("Remove Selected")
        self.remove_files_btn.clicked.connect(self.remove_selected_files)
        self.clear_files_btn = QPushButton("Clear All")
        self.clear_files_btn.clicked.connect(self.clear_queue)

        file_controls.addWidget(self.add_files_btn)
        file_controls.addWidget(self.remove_files_btn)
        file_controls.addWidget(self.clear_files_btn)

        files_layout.addLayout(file_controls)
        files_group.setLayout(files_layout)
        left_layout.addWidget(files_group)

        # Progress section
        progress_group = QGroupBox("Progress")
        progress_layout = QVBoxLayout()

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        progress_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Ready")
        progress_layout.addWidget(self.status_label)

        progress_group.setLayout(progress_layout)
        left_layout.addWidget(progress_group)

        # Action buttons
        buttons_layout = QHBoxLayout()
        self.run_button = QPushButton("Start Processing")
        self.run_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.stop_button = QPushButton("Stop")
        self.stop_button.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self.stop_button.setEnabled(False)

        self.run_button.clicked.connect(self.run_processing)
        self.stop_button.clicked.connect(self.stop_processing)

        buttons_layout.addWidget(self.run_button)
        buttons_layout.addWidget(self.stop_button)

        left_layout.addLayout(buttons_layout)

        splitter.addWidget(left_panel)

        # Right panel - Settings
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        # Create scrollable area for settings
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)

        # Add controls
        self.create_codec_controls(scroll_layout)
        self.create_processing_controls(scroll_layout)

        scroll.setWidget(scroll_content)
        right_layout.addWidget(scroll)

        splitter.addWidget(right_panel)

        # Set initial splitter sizes
        splitter.setSizes([400, 600])

    def create_codec_controls(self, parent_layout):
        # Video codec group
        video_group = QGroupBox("Video Codec")
        video_layout = QVBoxLayout()

        self.video_codec_group = QButtonGroup()
        codecs = [
            ("x264", "libx264"),
            ("x265", "libx265"),
            ("CUDA h264 (CQ)", "h264_nvenc"),
            ("CUDA HEVC (CQ)", "hevc_nvenc"),
            ("ProRes", "prores_ks"),
            ("Raw", "rawvideo"),
            ("Copy", "copy")
        ]

        for text, value in codecs:
            radio = QRadioButton(text)
            self.video_codec_group.addButton(radio)
            radio.setProperty("value", value)  # Store the value
            video_layout.addWidget(radio)
            if value == "libx265":  # Default selection
                radio.setChecked(True)

        video_group.setLayout(video_layout)
        parent_layout.addWidget(video_group)

        # Audio codec group
        audio_group = QGroupBox("Audio Codec")
        audio_layout = QVBoxLayout()

        self.audio_codec_group = QButtonGroup()
        audio_codecs = [
            ("LAME MP3", "libmp3lame"),
            ("AAC", "aac"),
            ("Opus", "libopus"),
            ("PCM32 (Raw)", "pcm_s32le"),
            ("Copy", "copy")
        ]

        for text, value in audio_codecs:
            radio = QRadioButton(text)
            self.audio_codec_group.addButton(radio)
            radio.setProperty("value", value)  # Store the value
            audio_layout.addWidget(radio)
            if value == "aac":  # Default selection
                radio.setChecked(True)

        audio_group.setLayout(audio_layout)
        parent_layout.addWidget(audio_group)

    def create_processing_controls(self, parent_layout):
        # Processing options
        processing_group = QGroupBox("Processing Options")
        processing_layout = QVBoxLayout()

        self.deinterlace_check = QCheckBox("Deinterlace")
        self.tff_check = QCheckBox("Top Field First")
        self.reduce_fps_check = QCheckBox("Reduce Frames")
        self.use_avisynth_check = QCheckBox("Use AviSynth+")
        self.use_ffms2_check = QCheckBox("Use ffms2 (No Frameserver, 1 Stream)")
        self.transcode_video_check = QCheckBox("Raw Transcode Video First")
        self.transcode_audio_check = QCheckBox("Raw Transcode Audio First")
        self.corrupt_check = QCheckBox("Fix AVC (ts) Corruption")
        self.stereo_check = QCheckBox("Reduce to Stereo")
        self.replace_check = QCheckBox("Replace Original Files")

        processing_layout.addWidget(self.deinterlace_check)
        processing_layout.addWidget(self.tff_check)
        processing_layout.addWidget(self.reduce_fps_check)
        processing_layout.addWidget(self.use_avisynth_check)
        processing_layout.addWidget(self.use_ffms2_check)
        processing_layout.addWidget(self.transcode_video_check)
        processing_layout.addWidget(self.transcode_audio_check)
        processing_layout.addWidget(self.corrupt_check)
        processing_layout.addWidget(self.stereo_check)
        processing_layout.addWidget(self.replace_check)

        # Connect checkbox signals
        self.deinterlace_check.clicked.connect(self.on_deinterlace_changed)
        self.tff_check.clicked.connect(self.on_tff_changed)
        self.reduce_fps_check.clicked.connect(self.on_reduce_fps_changed)
        self.use_avisynth_check.clicked.connect(self.on_avisynth_changed)
        self.use_ffms2_check.clicked.connect(self.on_ffms2_changed)

        processing_group.setLayout(processing_layout)
        parent_layout.addWidget(processing_group)

        # Sliders
        sliders_group = QGroupBox("Encoding Parameters")
        sliders_layout = QVBoxLayout()

        # Speed slider
        speed_layout = QHBoxLayout()
        speed_layout.addWidget(QLabel("Encoding Speed:"))
        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(0, 6)
        self.speed_slider.setValue(3)
        self.speed_value_label = QLabel("3")
        self.speed_slider.valueChanged.connect(lambda v: self.speed_value_label.setText(str(v)))
        speed_layout.addWidget(self.speed_slider)
        speed_layout.addWidget(self.speed_value_label)
        sliders_layout.addLayout(speed_layout)

        # CRF slider
        crf_layout = QHBoxLayout()
        crf_layout.addWidget(QLabel("CRF:"))
        self.crf_slider = QSlider(Qt.Horizontal)
        self.crf_slider.setRange(0, 51)
        self.crf_slider.setValue(23)
        self.crf_value_label = QLabel("23")
        self.crf_slider.valueChanged.connect(lambda v: self.crf_value_label.setText(str(v)))
        crf_layout.addWidget(self.crf_slider)
        crf_layout.addWidget(self.crf_value_label)
        sliders_layout.addLayout(crf_layout)

        # Audio bitrate slider
        abr_layout = QHBoxLayout()
        abr_layout.addWidget(QLabel("Audio ABR:"))
        self.abr_slider = QSlider(Qt.Horizontal)
        self.abr_slider.setRange(0, 512)
        self.abr_slider.setValue(256)
        self.abr_slider.setSingleStep(16)
        self.abr_value_label = QLabel("256k")
        self.abr_slider.valueChanged.connect(lambda v: self.abr_value_label.setText(f"{v}k"))
        abr_layout.addWidget(self.abr_slider)
        abr_layout.addWidget(self.abr_value_label)
        sliders_layout.addLayout(abr_layout)

        sliders_group.setLayout(sliders_layout)
        parent_layout.addWidget(sliders_group)

        # FFmpeg and AviSynth extras
        extras_group = QGroupBox("Additional Options")
        extras_layout = QVBoxLayout()

        ffmpeg_layout = QHBoxLayout()
        ffmpeg_layout.addWidget(QLabel("FFmpeg Extras:"))
        self.ffmpeg_extras_edit = QLineEdit()
        ffmpeg_layout.addWidget(self.ffmpeg_extras_edit)
        extras_layout.addLayout(ffmpeg_layout)

        avisynth_layout = QHBoxLayout()
        avisynth_layout.addWidget(QLabel("AviSynth+ Extras:"))
        self.avisynth_extras_edit = QTextEdit()
        self.avisynth_extras_edit.setMaximumHeight(60)
        avisynth_layout.addWidget(self.avisynth_extras_edit)
        extras_layout.addLayout(avisynth_layout)

        extras_group.setLayout(extras_layout)
        parent_layout.addWidget(extras_group)

    def show_about(self):
        QMessageBox.about(self,
                          "About Video Processor",
                          "Video Processor\n\n"
                          "A Qt-based video processing application.\n"
                          "Drag and drop files to process them.\n\n"
                          "Version 1.0")

    def select_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Input Files",
            "",
            "Video Files (*.mp4 *.avi *.mkv *.mov);;All Files (*.*)")

        if files:
            self.add_files(files)

    def add_files(self, file_paths):
        for path in file_paths:
            if os.path.isfile(path) and path not in self.file_queue:
                self.file_queue.append(path)
                item = QListWidgetItem(os.path.basename(path))
                item.setToolTip(path)
                self.file_list.addItem(item)

        self.update_ui_state()

    def update_ui_state(self):
        has_files = len(self.file_queue) > 0
        self.run_button.setEnabled(has_files)
        self.remove_files_btn.setEnabled(has_files)
        self.clear_files_btn.setEnabled(has_files)

    def get_selected_codec(self, button_group):
        selected = button_group.checkedButton()
        return selected.property("value") if selected else None

    def get_preset(self, speed_value):
        presets = {
            0: "veryslow",
            1: "slower",
            2: "slow",
            3: "medium",
            4: "fast",
            5: "faster",
            6: "ultrafast"
        }
        return presets.get(speed_value, "medium")

    # Checkbox event handlers
    def on_deinterlace_changed(self):
        if self.deinterlace_check.isChecked():
            self.use_avisynth_check.setChecked(True)
        elif not self.tff_check.isChecked() and not self.reduce_fps_check.isChecked():
            self.use_avisynth_check.setChecked(False)

    def on_tff_changed(self):
        if self.tff_check.isChecked():
            self.deinterlace_check.setChecked(True)
            self.use_avisynth_check.setChecked(True)

    def on_reduce_fps_changed(self):
        if self.reduce_fps_check.isChecked():
            self.deinterlace_check.setChecked(True)
            self.use_avisynth_check.setChecked(True)

    def on_avisynth_changed(self):
        if not self.use_avisynth_check.isChecked():
            self.tff_check.setChecked(False)
            self.deinterlace_check.setChecked(False)
            self.reduce_fps_check.setChecked(False)
            self.use_ffms2_check.setChecked(False)

    def on_ffms2_changed(self):
        if self.use_ffms2_check.isChecked():
            self.use_avisynth_check.setChecked(True)

    @pyqtSlot()
    def run_processing(self):
        if not self.file_queue:
            return

        self.process_thread = ProcessThread(self.file_queue, self)
        self.process_thread.progress_signal.connect(self.update_progress)
        self.process_thread.info_signal.connect(self.update_info)
        self.process_thread.finished_signal.connect(self.processing_finished)
        self.process_thread.file_started_signal.connect(self.file_started)
        self.process_thread.file_finished_signal.connect(self.file_finished)

        self.process_thread.start()

        # Update UI state
        self.run_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.add_files_btn.setEnabled(False)
        self.remove_files_btn.setEnabled(False)
        self.clear_files_btn.setEnabled(False)

        self.progress_bar.setMaximum(len(self.file_queue))
        self.progress_bar.setValue(0)

    @pyqtSlot()
    def stop_processing(self):
        if self.process_thread and self.process_thread.isRunning():
            self.process_thread.should_stop = True
            if self.process_thread.process:
                self.process_thread.process.kill()

            self.status_label.setText("Processing stopped")
            self.run_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.add_files_btn.setEnabled(True)
            self.remove_files_btn.setEnabled(True)
            self.clear_files_btn.setEnabled(True)

    @pyqtSlot(str, int)
    def update_progress(self, message, progress):
        self.status_label.setText(message)
        if progress > 0:
            self.progress_bar.setValue(progress)

    @pyqtSlot(str)
    def update_info(self, message):
        print(message)  # You might want to add a log window instead

    @pyqtSlot(int)
    def file_started(self, index):
        item = self.file_list.item(index)
        if item:
            item.setBackground(Qt.yellow)

    @pyqtSlot(int, bool)
    def file_finished(self, index, success):
        item = self.file_list.item(index)
        if item:
            item.setBackground(Qt.green if success else Qt.red)
        self.progress_bar.setValue(self.progress_bar.value() + 1)

    @pyqtSlot()
    def processing_finished(self):
        self.run_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.add_files_btn.setEnabled(True)
        self.remove_files_btn.setEnabled(True)
        self.clear_files_btn.setEnabled(True)
        self.status_label.setText("Processing complete")

    def remove_selected_files(self):
        selected_items = self.file_list.selectedItems()
        for item in selected_items:
            row = self.file_list.row(item)
            self.file_list.takeItem(row)
            self.file_queue.pop(row)

        self.update_ui_state()


    def clear_queue(self):
        self.file_list.clear()
        self.file_queue.clear()
        self.update_ui_state()

if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Set application style
    app.setStyle('Fusion')

    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


