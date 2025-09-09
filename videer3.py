#todo fix presets

import psutil
import sys
import os
import re
import time
import logging
import subprocess
import multiprocessing
import json
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                               QHBoxLayout, QLabel, QCheckBox, QRadioButton,
                               QPushButton, QLineEdit, QSlider, QTextEdit,
                               QFileDialog, QButtonGroup, QScrollArea, QGroupBox,
                               QListWidget, QStyle, QProgressBar, QSplitter,
                               QMenuBar, QMenu, QMessageBox, QListWidgetItem,
                               QTabWidget, QSpinBox, QComboBox, QGridLayout,
                               QFrame, QToolButton, QStatusBar)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QSize, QSettings, QTimer
from PySide6.QtGui import QIcon, QDrag, QDragEnterEvent, QDropEvent, QAction, QFont, QPalette, QColor


def find_ffmpeg():
    """Find FFmpeg executable in system PATH or current directory"""
    import shutil
    import os

    # First check if ffmpeg is in system PATH
    ffmpeg_path = shutil.which('ffmpeg')
    if ffmpeg_path:
        return ffmpeg_path

    # Then check current directory
    current_dir = os.path.dirname(os.path.abspath(__file__))
    ffmpeg_local = os.path.join(current_dir, 'ffmpeg.exe')
    if os.path.exists(ffmpeg_local):
        return ffmpeg_local

    return None


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
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
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
            self.main_window.add_files(links)
        else:
            super().dropEvent(event)


class ProcessThread(QThread):
    progress_signal = Signal(str, int)  # message, progress percentage
    info_signal = Signal(str)
    finished_signal = Signal()
    file_started_signal = Signal(int)  # file index
    file_finished_signal = Signal(int, bool)  # file index, success
    time_remaining_signal = Signal(str)  # estimated time remaining

    def __init__(self, file_queue, app_window):
        super().__init__()
        self.file_queue = file_queue
        self.app_window = app_window
        self.should_stop = False
        self.process = None
        self.process_pid = None
        self.start_time = None

    def run(self):
        self.start_time = time.time()
        self.queue(self.file_queue)
        self.finished_signal.emit()

    def queue(self, files):
        for i, file in enumerate(files):
            if self.should_stop:
                return

            fileobj = File((i, file))
            fileobj.create_logger()

            # Set output name based on current settings
            output_format = self.app_window.output_format_combo.currentText().lower()
            fileobj.extension = f".{output_format}"

            fileobj.outputname = f"{fileobj.basename}_{self.app_window.crf_value.value()}" \
                                 f"{self.app_window.get_selected_codec(self.app_window.video_codec_group)}_" \
                                 f"{self.app_window.get_selected_codec(self.app_window.audio_codec_group)}" \
                                 f"{self.app_window.abr_value.value()}{fileobj.extension}"

            self.file_started_signal.emit(i)
            self.info_signal.emit(f"Processing {i + 1}/{len(files)}: {fileobj.displayname}")

            # Calculate estimated time remaining
            if i > 0:
                elapsed = time.time() - self.start_time
                avg_time_per_file = elapsed / i
                remaining_files = len(files) - i
                est_remaining = avg_time_per_file * remaining_files

                hours = int(est_remaining // 3600)
                minutes = int((est_remaining % 3600) // 60)
                seconds = int(est_remaining % 60)

                if hours > 0:
                    time_str = f"{hours}h {minutes}m {seconds}s"
                elif minutes > 0:
                    time_str = f"{minutes}m {seconds}s"
                else:
                    time_str = f"{seconds}s"

                self.time_remaining_signal.emit(f"Est. remaining: {time_str}")

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
            elif success:
                # Preserve timestamps even if not replacing
                self.preserve_timestamps(fileobj.orig_name, fileobj.outputname, fileobj.log)

            # Cleanup temporary files
            self.cleanup_temp_files(fileobj)

            if fileobj.ffmpeg_errors:
                self.info_signal.emit("Errors:\n" + '\n'.join(fileobj.ffmpeg_errors))

    def transcode(self, fileobj, transcode_video, transcode_audio):
        fileobj.log.info("Transcode process started")
        preset = self.app_window.get_preset()

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

        preset = self.app_window.get_preset()
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
                command.append(f'-cq {self.app_window.crf_value.value()}')
            else:
                command.append(f'-crf {self.app_window.crf_value.value()}')

        # Audio codec settings
        audio_codec = self.app_window.get_selected_codec(self.app_window.audio_codec_group)
        if audio_codec == "copy":
            command.append('-c:a copy')
        else:
            command.append(f'-c:a {audio_codec}')
            command.append(f'-b:a {self.app_window.abr_value.value()}k')

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

        # Container-specific options
        output_format = self.app_window.output_format_combo.currentText().lower()
        if output_format == "mp4":
            command.append('-movflags +faststart')

        command.append('-bf 2')
        command.append('-flags +cgop')
        command.append('-pix_fmt yuv420p')

        # Set output format
        if output_format == "mkv":
            command.append(f'-f matroska "{fileobj.outputname}"')
        elif output_format == "mp4":
            command.append(f'-f mp4 "{fileobj.outputname}"')
        elif output_format == "avi":
            command.append(f'-f avi "{fileobj.outputname}"')
        else:
            command.append(f'"{fileobj.outputname}"')

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

            # CRITICAL: Multi-threading setup BEFORE source loading
            avsfile.write('SetFilterMTMode("DEFAULT_MT_MODE", 2)\n')
            avsfile.write('SetFilterMTMode("QTGMC", 3)\n')
            avsfile.write('SetFilterMTMode("nnedi3", 3)\n')
            avsfile.write('SetFilterMTMode("MVAnalyse", 3)\n')
            avsfile.write('SetFilterMTMode("MVDegrain1", 3)\n')
            avsfile.write('SetFilterMTMode("MVDegrain2", 3)\n')
            avsfile.write('SetFilterMTMode("MVDegrain3", 3)\n')

            # Source - FIXED to include audio with FFMS2
            if self.app_window.use_ffms2_check.isChecked():
                # Load video
                avsfile.write(f'v = FFVideoSource("{fileobj.filename}", track=-1)\n')
                # Load audio
                avsfile.write(f'a = FFAudioSource("{fileobj.filename}", track=-1)\n')
                # Combine video and audio
                avsfile.write('AudioDub(v, a)\n')
                avsfile.write('SetFilterMTMode("FFVideoSource", 3)\n')
            else:
                avsfile.write(f'AVISource("{fileobj.filename}", audio=true)\n')

            avsfile.write('ConvertToYV24(matrix="rec709")\n')

            # Add any custom AviSynth extras
            if self.app_window.avisynth_extras_edit.toPlainText().strip():
                avsfile.write(self.app_window.avisynth_extras_edit.toPlainText() + '\n')

            if self.app_window.tff_check.isChecked():
                avsfile.write('AssumeTFF()\n')

            # QTGMC deinterlacing - only when enabled
            if self.app_window.deinterlace_check.isChecked():
                preset = self.app_window.get_preset()
                if self.app_window.reduce_fps_check.isChecked():
                    avsfile.write(f'QTGMC(Preset="{preset}", FPSDivisor=2, EdiThreads={multiprocessing.cpu_count()})\n')
                else:
                    avsfile.write(f'QTGMC(Preset="{preset}", EdiThreads={multiprocessing.cpu_count()})\n')

            # Prefetch at the end
            avsfile.write(f'Prefetch({multiprocessing.cpu_count()})\n')

    def preserve_timestamps(self, source_file, dest_file, log):
        """
        Preserve timestamps from the source file to the destination file across different platforms
        """
        try:
            # Get original file timestamps
            orig_stat = os.stat(source_file)
            orig_atime = orig_stat.st_atime
            orig_mtime = orig_stat.st_mtime
            orig_ctime = orig_stat.st_ctime

            # Create datetime objects for timestamps
            import datetime
            import platform

            # Convert timestamps to datetime objects
            creation_time = datetime.datetime.fromtimestamp(orig_ctime)
            access_time = datetime.datetime.fromtimestamp(orig_atime)
            mod_time = datetime.datetime.fromtimestamp(orig_mtime)

            # Restore access and modification times
            os.utime(dest_file, (orig_atime, orig_mtime))

            # Handle creation time for different platforms
            if platform.system() == 'Windows':
                # Use PowerShell to set creation time on Windows
                powershell_cmd = f'(Get-Item "{dest_file}").CreationTime = (Get-Date "{creation_time}")'
                subprocess.run(['powershell', '-Command', powershell_cmd], check=True)
            elif platform.system() == 'Darwin':  # macOS
                # Use SetFile for macOS
                subprocess.run(['SetFile', '-d', creation_time.strftime("%m/%d/%Y %H:%M:%S"), dest_file], check=True)
            # For Linux, creation time is typically not directly modifiable

            log.info(f"Preserved file timestamps: "
                     f"Access={access_time}, "
                     f"Modified={mod_time}, "
                     f"Creation={creation_time}")
        except Exception as e:
            log.warning(f"Error preserving timestamps: {e}")

    def replace_file(self, rename_from, rename_to, log):
        log.info(f"Replacing {rename_to} with {rename_from}")
        if os.path.exists(rename_from):
            # Get original file timestamps
            orig_stat = os.stat(rename_to)
            orig_atime = orig_stat.st_atime
            orig_mtime = orig_stat.st_mtime

            # Perform the file replacement while preserving the original extension
            orig_extension = os.path.splitext(rename_to)[1]
            old_file_name = f"{rename_to}.old{orig_extension}"
            os.rename(rename_to, old_file_name)
            os.rename(rename_from, rename_to)

            # Restore original timestamps
            os.utime(rename_to, (orig_atime, orig_mtime))
            log.info(f"Preserved original file timestamps: Access={orig_atime}, Modified={orig_mtime}")

    def cleanup_temp_files(self, fileobj):
        if os.path.exists(fileobj.transcodename):
            os.remove(fileobj.transcodename)
        if os.path.exists(fileobj.ffindex):
            os.remove(fileobj.ffindex)
        if os.path.exists(fileobj.tempffindex):
            os.remove(fileobj.tempffindex)
        if os.path.exists(fileobj.avsfile):
            os.remove(fileobj.avsfile)

    def stop_process(self):
        """Kill the process and all its children"""
        if self.process_pid:
            try:
                parent = psutil.Process(self.process_pid)
                children = parent.children(recursive=True)

                # Kill children first
                for child in children:
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass

                # Kill parent
                try:
                    parent.kill()
                except psutil.NoSuchProcess:
                    pass

                self.process_pid = None
            except psutil.NoSuchProcess:
                pass

    def open_process(self, command_line, fileobj):
        fileobj.log.info(f"Executing command {command_line}")

        # Find FFmpeg path
        ffmpeg_path = find_ffmpeg()
        if not ffmpeg_path:
            fileobj.log.error("FFmpeg not found in PATH or current directory")
            return 1

        # Replace ffmpeg.exe with full path
        command_line = command_line.replace('ffmpeg.exe', f'"{ffmpeg_path}"')

        try:
            with subprocess.Popen(command_line,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT,
                                  universal_newlines=True,
                                  encoding="utf8",
                                  shell=True) as self.process:

                self.process_pid = self.process.pid
                errors = ["error", "invalid"]

                for line in self.process.stdout:
                    if self.should_stop:
                        self.stop_process()
                        return 1

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
            self.process_pid = None
            fileobj.log.info(f"Return code: {return_code}")
            return return_code

        except Exception as e:
            fileobj.log.error(f"Error executing FFmpeg: {str(e)}")
            return 1


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Processor Pro")
        self.file_queue = []
        self.process_thread = None
        self.setAcceptDrops(True)
        self.settings = QSettings("VideoProcessor", "Settings")

        # Set window icon
        self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))

        # Initialize UI
        self.create_menu_bar()
        self.initUI()
        self.create_status_bar()

        # Load saved settings
        self.load_settings()

        # Set minimum window size
        self.setMinimumWidth(1200)
        self.setMinimumHeight(900)

    def create_menu_bar(self):
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu('File')

        add_files_action = QAction('Add Files', self)
        add_files_action.setShortcut('Ctrl+O')
        add_files_action.triggered.connect(self.select_files)
        file_menu.addAction(add_files_action)

        add_folder_action = QAction('Add Folder', self)
        add_folder_action.setShortcut('Ctrl+Shift+O')
        add_folder_action.triggered.connect(self.select_folder)
        file_menu.addAction(add_folder_action)

        file_menu.addSeparator()

        clear_queue_action = QAction('Clear Queue', self)
        clear_queue_action.triggered.connect(self.clear_queue)
        file_menu.addAction(clear_queue_action)

        file_menu.addSeparator()

        exit_action = QAction('Exit', self)
        exit_action.setShortcut('Ctrl+Q')
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Presets menu
        presets_menu = menubar.addMenu('Presets')

        save_preset_action = QAction('Save Current Settings as Preset', self)
        save_preset_action.triggered.connect(self.save_preset)
        presets_menu.addAction(save_preset_action)

        load_preset_action = QAction('Load Preset', self)
        load_preset_action.triggered.connect(self.load_preset)
        presets_menu.addAction(load_preset_action)

        presets_menu.addSeparator()

        # Add some default presets
        web_preset = QAction('Web Quality (H.264/AAC)', self)
        web_preset.triggered.connect(lambda: self.apply_web_preset())
        presets_menu.addAction(web_preset)

        hq_preset = QAction('High Quality (H.265/Opus)', self)
        hq_preset.triggered.connect(lambda: self.apply_hq_preset())
        presets_menu.addAction(hq_preset)

        archive_preset = QAction('Archive (ProRes/PCM)', self)
        archive_preset.triggered.connect(lambda: self.apply_archive_preset())
        presets_menu.addAction(archive_preset)

        # Help menu
        help_menu = menubar.addMenu('Help')

        about_action = QAction('About', self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def create_status_bar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        # Add permanent widgets to status bar
        self.ffmpeg_status = QLabel("FFmpeg: Checking...")
        self.status_bar.addPermanentWidget(self.ffmpeg_status)

        # Check FFmpeg status
        self.check_ffmpeg_status()

    def check_ffmpeg_status(self):
        if find_ffmpeg():
            self.ffmpeg_status.setText("FFmpeg: ✓ Found")
            self.ffmpeg_status.setStyleSheet("color: green;")
        else:
            self.ffmpeg_status.setText("FFmpeg: ✗ Not Found")
            self.ffmpeg_status.setStyleSheet("color: red;")

    def initUI(self):
        # Create main widget and layout
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # Create splitter for resizable sections
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # Left panel - File list and controls
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        # File list section with enhanced header
        files_group = QGroupBox("Input Files")
        files_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 2px solid #cccccc;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)
        files_layout = QVBoxLayout()

        # File count label
        self.file_count_label = QLabel("0 files in queue")
        self.file_count_label.setStyleSheet("font-weight: normal; color: #666;")
        files_layout.addWidget(self.file_count_label)

        # Add file list widget
        self.file_list = FileListWidget(main_window=self)
        files_layout.addWidget(self.file_list)

        # File controls with icons
        file_controls = QHBoxLayout()

        self.add_files_btn = QPushButton("Add Files")
        self.add_files_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder))
        self.add_files_btn.clicked.connect(self.select_files)

        self.add_folder_btn = QPushButton("Add Folder")
        self.add_folder_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
        self.add_folder_btn.clicked.connect(self.select_folder)

        self.remove_files_btn = QPushButton("Remove")
        self.remove_files_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        self.remove_files_btn.clicked.connect(self.remove_selected_files)

        self.clear_files_btn = QPushButton("Clear All")
        self.clear_files_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCancelButton))
        self.clear_files_btn.clicked.connect(self.clear_queue)

        file_controls.addWidget(self.add_files_btn)
        file_controls.addWidget(self.add_folder_btn)
        file_controls.addWidget(self.remove_files_btn)
        file_controls.addWidget(self.clear_files_btn)

        files_layout.addLayout(file_controls)
        files_group.setLayout(files_layout)
        left_layout.addWidget(files_group)

        # Progress section with enhanced display
        progress_group = QGroupBox("Progress")
        progress_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 2px solid #cccccc;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
        """)
        progress_layout = QVBoxLayout()

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 2px solid grey;
                border-radius: 5px;
                text-align: center;
                height: 25px;
            }
            QProgressBar::chunk {
                background-color: #0078D7;
                border-radius: 3px;
            }
        """)
        progress_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("font-weight: normal;")
        progress_layout.addWidget(self.status_label)

        self.time_remaining_label = QLabel("")
        self.time_remaining_label.setStyleSheet("font-weight: normal; color: #666;")
        progress_layout.addWidget(self.time_remaining_label)

        progress_group.setLayout(progress_layout)
        left_layout.addWidget(progress_group)

        # Action buttons with enhanced styling
        buttons_layout = QHBoxLayout()
        self.run_button = QPushButton("Start Processing")
        self.run_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.run_button.setStyleSheet("""
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

        self.stop_button = QPushButton("Stop")
        self.stop_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_button.setEnabled(False)
        self.stop_button.setStyleSheet("""
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

        self.run_button.clicked.connect(self.run_processing)
        self.stop_button.clicked.connect(self.stop_processing)

        buttons_layout.addWidget(self.run_button)
        buttons_layout.addWidget(self.stop_button)

        left_layout.addLayout(buttons_layout)

        # Add stretch to push everything to the top
        left_layout.addStretch()

        splitter.addWidget(left_panel)

        # Right panel - Settings in tabs
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        # Create tab widget
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 2px solid #cccccc;
                background-color: white;
            }
            QTabBar::tab {
                background-color: #f0f0f0;
                padding: 8px 16px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: white;
                border-bottom: 2px solid #0078D7;
            }
            QTabBar::tab:hover {
                background-color: #e0e0e0;
            }
        """)

        # Create tabs
        self.create_video_tab()
        self.create_audio_tab()
        self.create_processing_tab()
        self.create_advanced_tab()
        self.create_output_tab()

        right_layout.addWidget(self.tabs)
        splitter.addWidget(right_panel)

        # Set initial splitter sizes
        splitter.setSizes([500, 700])

    def create_video_tab(self):
        video_widget = QWidget()
        video_layout = QVBoxLayout()

        # Video codec group
        codec_group = QGroupBox("Video Codec")
        codec_layout = QVBoxLayout()

        self.video_codec_group = QButtonGroup()
        codecs = [
            ("H.264 (x264)", "libx264"),
            ("H.265/HEVC (x265)", "libx265"),
            ("NVIDIA H.264 (NVENC)", "h264_nvenc"),
            ("NVIDIA H.265/HEVC (NVENC)", "hevc_nvenc"),
            ("ProRes", "prores_ks"),
            ("Raw/Uncompressed", "rawvideo"),
            ("Copy (No Re-encoding)", "copy")
        ]

        for text, value in codecs:
            radio = QRadioButton(text)
            self.video_codec_group.addButton(radio)
            radio.setProperty("value", value)
            codec_layout.addWidget(radio)
            if value == "libx265":
                radio.setChecked(True)

        codec_group.setLayout(codec_layout)
        video_layout.addWidget(codec_group)

        # Video quality settings
        quality_group = QGroupBox("Quality Settings")
        quality_layout = QGridLayout()

        # Preset
        quality_layout.addWidget(QLabel("Encoding Speed:"), 0, 0)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(["Ultra Fast", "Super Fast", "Very Fast",
                                    "Faster", "Fast", "Medium", "Slow",
                                    "Slower", "Very Slow"])
        self.preset_combo.setCurrentIndex(5)  # Medium
        quality_layout.addWidget(self.preset_combo, 0, 1)

        # CRF with spinbox
        quality_layout.addWidget(QLabel("CRF (Quality):"), 1, 0)
        crf_widget = QWidget()
        crf_layout = QHBoxLayout(crf_widget)
        crf_layout.setContentsMargins(0, 0, 0, 0)

        self.crf_slider = QSlider(Qt.Orientation.Horizontal)
        self.crf_slider.setRange(0, 51)
        self.crf_slider.setValue(23)

        self.crf_value = QSpinBox()
        self.crf_value.setRange(0, 51)
        self.crf_value.setValue(23)

        # Connect slider and spinbox
        self.crf_slider.valueChanged.connect(self.crf_value.setValue)
        self.crf_value.valueChanged.connect(self.crf_slider.setValue)

        crf_layout.addWidget(self.crf_slider)
        crf_layout.addWidget(self.crf_value)
        quality_layout.addWidget(crf_widget, 1, 1)

        # Help text for CRF
        crf_help = QLabel("Lower = Better Quality (17-28 recommended)")
        crf_help.setStyleSheet("color: #666; font-size: 10px;")
        quality_layout.addWidget(crf_help, 2, 1)

        quality_group.setLayout(quality_layout)
        video_layout.addWidget(quality_group)

        video_layout.addStretch()
        video_widget.setLayout(video_layout)
        self.tabs.addTab(video_widget, "Video")

    def create_audio_tab(self):
        audio_widget = QWidget()
        audio_layout = QVBoxLayout()

        # Audio codec group
        codec_group = QGroupBox("Audio Codec")
        codec_layout = QVBoxLayout()

        self.audio_codec_group = QButtonGroup()
        audio_codecs = [
            ("AAC", "aac"),
            ("MP3 (LAME)", "libmp3lame"),
            ("Opus", "libopus"),
            ("AC3", "ac3"),
            ("FLAC (Lossless)", "flac"),
            ("PCM (Uncompressed)", "pcm_s32le"),
            ("Copy (No Re-encoding)", "copy")
        ]

        for text, value in audio_codecs:
            radio = QRadioButton(text)
            self.audio_codec_group.addButton(radio)
            radio.setProperty("value", value)
            codec_layout.addWidget(radio)
            if value == "aac":
                radio.setChecked(True)

        codec_group.setLayout(codec_layout)
        audio_layout.addWidget(codec_group)

        # Audio quality settings
        quality_group = QGroupBox("Audio Settings")
        quality_layout = QGridLayout()

        # Bitrate with spinbox
        quality_layout.addWidget(QLabel("Bitrate:"), 0, 0)
        abr_widget = QWidget()
        abr_layout = QHBoxLayout(abr_widget)
        abr_layout.setContentsMargins(0, 0, 0, 0)

        self.abr_slider = QSlider(Qt.Orientation.Horizontal)
        self.abr_slider.setRange(32, 512)
        self.abr_slider.setValue(256)
        self.abr_slider.setSingleStep(16)

        self.abr_value = QSpinBox()
        self.abr_value.setRange(32, 512)
        self.abr_value.setValue(256)
        self.abr_value.setSingleStep(16)
        self.abr_value.setSuffix(" kbps")

        # Connect slider and spinbox
        self.abr_slider.valueChanged.connect(self.abr_value.setValue)
        self.abr_value.valueChanged.connect(self.abr_slider.setValue)

        abr_layout.addWidget(self.abr_slider)
        abr_layout.addWidget(self.abr_value)
        quality_layout.addWidget(abr_widget, 0, 1)

        # Channel configuration
        quality_layout.addWidget(QLabel("Channels:"), 1, 0)
        self.stereo_check = QCheckBox("Force Stereo (2.0)")
        quality_layout.addWidget(self.stereo_check, 1, 1)

        quality_group.setLayout(quality_layout)
        audio_layout.addWidget(quality_group)

        audio_layout.addStretch()
        audio_widget.setLayout(audio_layout)
        self.tabs.addTab(audio_widget, "Audio")

    def create_processing_tab(self):
        processing_widget = QWidget()
        processing_layout = QVBoxLayout()

        # Deinterlacing options
        deinterlace_group = QGroupBox("Deinterlacing")
        deinterlace_layout = QVBoxLayout()

        self.deinterlace_check = QCheckBox("Enable Deinterlacing")
        self.tff_check = QCheckBox("Top Field First")
        self.reduce_fps_check = QCheckBox("Reduce Frame Rate (Halve FPS)")

        deinterlace_layout.addWidget(self.deinterlace_check)
        deinterlace_layout.addWidget(self.tff_check)
        deinterlace_layout.addWidget(self.reduce_fps_check)

        deinterlace_group.setLayout(deinterlace_layout)
        processing_layout.addWidget(deinterlace_group)

        # AviSynth options
        avisynth_group = QGroupBox("AviSynth+ Processing")
        avisynth_layout = QVBoxLayout()

        self.use_avisynth_check = QCheckBox("Use AviSynth+")
        self.use_ffms2_check = QCheckBox("Use FFMS2 Source Filter")
        self.use_ffms2_check.setChecked(True)  # Enable FFMS2 by default
        self.use_avisynth_check.setChecked(True)  # Also enable AviSynth+ since FFMS2 requires it

        avisynth_layout.addWidget(self.use_avisynth_check)
        avisynth_layout.addWidget(self.use_ffms2_check)

        # AviSynth extras
        avisynth_layout.addWidget(QLabel("Custom AviSynth Script:"))
        self.avisynth_extras_edit = QTextEdit()
        self.avisynth_extras_edit.setMaximumHeight(100)
        self.avisynth_extras_edit.setPlaceholderText("Add custom AviSynth commands here...")
        avisynth_layout.addWidget(self.avisynth_extras_edit)

        avisynth_group.setLayout(avisynth_layout)
        processing_layout.addWidget(avisynth_group)

        # Pre-processing options
        preprocess_group = QGroupBox("Pre-processing")
        preprocess_layout = QVBoxLayout()

        self.transcode_video_check = QCheckBox("Transcode to Raw Video First")
        self.transcode_audio_check = QCheckBox("Transcode to Raw Audio First")

        preprocess_layout.addWidget(self.transcode_video_check)
        preprocess_layout.addWidget(self.transcode_audio_check)

        preprocess_group.setLayout(preprocess_layout)
        processing_layout.addWidget(preprocess_group)

        # Connect checkbox signals
        self.deinterlace_check.clicked.connect(self.on_deinterlace_changed)
        self.tff_check.clicked.connect(self.on_tff_changed)
        self.reduce_fps_check.clicked.connect(self.on_reduce_fps_changed)
        self.use_avisynth_check.clicked.connect(self.on_avisynth_changed)
        self.use_ffms2_check.clicked.connect(self.on_ffms2_changed)

        processing_layout.addStretch()
        processing_widget.setLayout(processing_layout)
        self.tabs.addTab(processing_widget, "Processing")

    def create_advanced_tab(self):
        advanced_widget = QWidget()
        advanced_layout = QVBoxLayout()

        # Fixes and workarounds
        fixes_group = QGroupBox("Fixes & Workarounds")
        fixes_layout = QVBoxLayout()

        self.corrupt_check = QCheckBox("Fix H.264 Stream Corruption (TS files)")
        fixes_layout.addWidget(self.corrupt_check)

        fixes_group.setLayout(fixes_layout)
        advanced_layout.addWidget(fixes_group)

        # FFmpeg extras
        ffmpeg_group = QGroupBox("FFmpeg Options")
        ffmpeg_layout = QVBoxLayout()

        ffmpeg_layout.addWidget(QLabel("Additional FFmpeg Parameters:"))
        self.ffmpeg_extras_edit = QLineEdit()
        self.ffmpeg_extras_edit.setPlaceholderText("-tune film -x264-params keyint=250:min-keyint=25")
        ffmpeg_layout.addWidget(self.ffmpeg_extras_edit)

        ffmpeg_group.setLayout(ffmpeg_layout)
        advanced_layout.addWidget(ffmpeg_group)

        # Thread settings
        thread_group = QGroupBox("Performance")
        thread_layout = QGridLayout()

        thread_layout.addWidget(QLabel("CPU Threads:"), 0, 0)
        self.thread_spin = QSpinBox()
        self.thread_spin.setRange(1, multiprocessing.cpu_count())
        self.thread_spin.setValue(multiprocessing.cpu_count())
        thread_layout.addWidget(self.thread_spin, 0, 1)

        thread_help = QLabel(f"Available: {multiprocessing.cpu_count()} cores")
        thread_help.setStyleSheet("color: #666; font-size: 10px;")
        thread_layout.addWidget(thread_help, 1, 1)

        thread_group.setLayout(thread_layout)
        advanced_layout.addWidget(thread_group)

        advanced_layout.addStretch()
        advanced_widget.setLayout(advanced_layout)
        self.tabs.addTab(advanced_widget, "Advanced")

    def create_output_tab(self):
        output_widget = QWidget()
        output_layout = QVBoxLayout()

        # Output format
        format_group = QGroupBox("Output Format")
        format_layout = QGridLayout()

        format_layout.addWidget(QLabel("Container:"), 0, 0)
        self.output_format_combo = QComboBox()
        self.output_format_combo.addItems(["MKV", "MP4", "AVI", "MOV", "WebM"])
        format_layout.addWidget(self.output_format_combo, 0, 1)

        format_group.setLayout(format_layout)
        output_layout.addWidget(format_group)

        # File handling
        file_group = QGroupBox("File Handling")
        file_layout = QVBoxLayout()

        self.replace_check = QCheckBox("Replace Original Files")
        self.replace_check.setStyleSheet("color: #d9534f; font-weight: bold;")
        file_layout.addWidget(self.replace_check)

        replace_warning = QLabel("⚠ Warning: Original files will be renamed to .old extension")
        replace_warning.setStyleSheet("color: #856404; background-color: #fff3cd; padding: 5px; border-radius: 3px;")
        file_layout.addWidget(replace_warning)

        file_group.setLayout(file_layout)
        output_layout.addWidget(file_group)

        # Output directory
        dir_group = QGroupBox("Output Directory")
        dir_layout = QVBoxLayout()

        self.same_dir_radio = QRadioButton("Same as source file")
        self.same_dir_radio.setChecked(True)
        self.custom_dir_radio = QRadioButton("Custom directory:")

        dir_layout.addWidget(self.same_dir_radio)
        dir_layout.addWidget(self.custom_dir_radio)

        custom_dir_widget = QWidget()
        custom_dir_layout = QHBoxLayout(custom_dir_widget)
        custom_dir_layout.setContentsMargins(20, 0, 0, 0)

        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setEnabled(False)
        self.browse_dir_btn = QPushButton("Browse...")
        self.browse_dir_btn.setEnabled(False)
        self.browse_dir_btn.clicked.connect(self.browse_output_dir)

        custom_dir_layout.addWidget(self.output_dir_edit)
        custom_dir_layout.addWidget(self.browse_dir_btn)

        dir_layout.addWidget(custom_dir_widget)

        # Connect radio buttons
        self.custom_dir_radio.toggled.connect(lambda checked: self.output_dir_edit.setEnabled(checked))
        self.custom_dir_radio.toggled.connect(lambda checked: self.browse_dir_btn.setEnabled(checked))

        dir_group.setLayout(dir_layout)
        output_layout.addWidget(dir_group)

        output_layout.addStretch()
        output_widget.setLayout(output_layout)
        self.tabs.addTab(output_widget, "Output")

    def browse_output_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if directory:
            self.output_dir_edit.setText(directory)

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            video_extensions = ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg']
            files = []
            for file in os.listdir(folder):
                if any(file.lower().endswith(ext) for ext in video_extensions):
                    files.append(os.path.join(folder, file))
            if files:
                self.add_files(files)

    def get_preset(self):
        preset_map = {
            "Ultra Fast": "ultrafast",
            "Super Fast": "superfast",
            "Very Fast": "veryfast",
            "Faster": "faster",
            "Fast": "fast",
            "Medium": "medium",
            "Slow": "slow",
            "Slower": "slower",
            "Very Slow": "veryslow"
        }
        return preset_map.get(self.preset_combo.currentText(), "medium")

    def apply_web_preset(self):
        # Set H.264 video codec
        for button in self.video_codec_group.buttons():
            if button.property("value") == "libx264":
                button.setChecked(True)
                break

        # Set AAC audio codec
        for button in self.audio_codec_group.buttons():
            if button.property("value") == "aac":
                button.setChecked(True)
                break

        # Set quality settings
        self.crf_value.setValue(23)
        self.abr_value.setValue(192)
        self.preset_combo.setCurrentText("Fast")
        self.output_format_combo.setCurrentText("MP4")

    def apply_hq_preset(self):
        # Set H.265 video codec
        for button in self.video_codec_group.buttons():
            if button.property("value") == "libx265":
                button.setChecked(True)
                break

        # Set Opus audio codec
        for button in self.audio_codec_group.buttons():
            if button.property("value") == "libopus":
                button.setChecked(True)
                break

        # Set quality settings
        self.crf_value.setValue(18)
        self.abr_value.setValue(256)
        self.preset_combo.setCurrentText("Slow")
        self.output_format_combo.setCurrentText("MKV")

    def apply_archive_preset(self):
        # Set ProRes video codec
        for button in self.video_codec_group.buttons():
            if button.property("value") == "prores_ks":
                button.setChecked(True)
                break

        # Set PCM audio codec
        for button in self.audio_codec_group.buttons():
            if button.property("value") == "pcm_s32le":
                button.setChecked(True)
                break

        # Set quality settings
        self.crf_value.setValue(10)
        self.preset_combo.setCurrentText("Medium")
        self.output_format_combo.setCurrentText("MOV")

    def save_preset(self):
        # Get preset name from user
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "Save Preset", "Enter preset name:")
        if ok and name:
            preset = {
                "video_codec": self.get_selected_codec(self.video_codec_group),
                "audio_codec": self.get_selected_codec(self.audio_codec_group),
                "crf": self.crf_value.value(),
                "abr": self.abr_value.value(),
                "preset": self.preset_combo.currentText(),
                "output_format": self.output_format_combo.currentText(),
                "stereo": self.stereo_check.isChecked(),
                #"stereo_mode": self.stereo_mode_combo.currentText(),
                "deinterlace": self.deinterlace_check.isChecked(),
                "tff": self.tff_check.isChecked(),
                "reduce_fps": self.reduce_fps_check.isChecked(),
                "use_avisynth": self.use_avisynth_check.isChecked(),
                "use_ffms2": self.use_ffms2_check.isChecked(),
                "transcode_video": self.transcode_video_check.isChecked(),
                "transcode_audio": self.transcode_audio_check.isChecked(),
                "corrupt_fix": self.corrupt_check.isChecked(),
                "replace_files": self.replace_check.isChecked(),
                "ffmpeg_extras": self.ffmpeg_extras_edit.text(),
                "avisynth_extras": self.avisynth_extras_edit.toPlainText()
            }

            # Save to file
            preset_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"preset_{name}.json")
            with open(preset_file, 'w') as f:
                json.dump(preset, f, indent=4)

            QMessageBox.information(self, "Preset Saved", f"Preset '{name}' saved successfully!")

    def load_preset(self):
        # Get list of available presets
        preset_dir = os.path.dirname(os.path.abspath(__file__))
        preset_files = [f for f in os.listdir(preset_dir) if f.startswith("preset_") and f.endswith(".json")]

        if not preset_files:
            QMessageBox.warning(self, "No Presets", "No saved presets found.")
            return

        # Let user choose preset
        from PySide6.QtWidgets import QInputDialog

        preset_names = [f.replace("preset_", "").replace(".json", "") for f in preset_files]
        name, ok = QInputDialog.getItem(self, "Load Preset", "Select preset:", preset_names, 0, False)

        if ok and name:
            preset_file = os.path.join(preset_dir, f"preset_{name}.json")

            try:
                with open(preset_file, 'r') as f:
                    preset = json.load(f)

                # Apply video codec
                if preset.get("video_codec"):
                    for button in self.video_codec_group.buttons():
                        if button.property("value") == preset["video_codec"]:
                            button.setChecked(True)
                            break

                # Apply audio codec
                if preset.get("audio_codec"):
                    for button in self.audio_codec_group.buttons():
                        if button.property("value") == preset["audio_codec"]:
                            button.setChecked(True)
                            break

                # Apply other settings
                if "crf" in preset:
                    self.crf_value.setValue(preset["crf"])
                if "abr" in preset:
                    self.abr_value.setValue(preset["abr"])
                if "preset" in preset:
                    index = self.preset_combo.findText(preset["preset"])
                    if index >= 0:
                        self.preset_combo.setCurrentIndex(index)
                if "output_format" in preset:
                    index = self.output_format_combo.findText(preset["output_format"])
                    if index >= 0:
                        self.output_format_combo.setCurrentIndex(index)
                #if "stereo_mode" in preset:
                #    index = self.stereo_mode_combo.findText(preset["stereo_mode"])
                #    if index >= 0:
                #        self.stereo_mode_combo.setCurrentIndex(index)

                # Apply checkboxes
                if "stereo" in preset:
                    self.stereo_check.setChecked(preset["stereo"])
                if "deinterlace" in preset:
                    self.deinterlace_check.setChecked(preset["deinterlace"])
                if "tff" in preset:
                    self.tff_check.setChecked(preset["tff"])
                if "reduce_fps" in preset:
                    self.reduce_fps_check.setChecked(preset["reduce_fps"])
                if "use_avisynth" in preset:
                    self.use_avisynth_check.setChecked(preset["use_avisynth"])
                if "use_ffms2" in preset:
                    self.use_ffms2_check.setChecked(preset["use_ffms2"])
                if "transcode_video" in preset:
                    self.transcode_video_check.setChecked(preset["transcode_video"])
                if "transcode_audio" in preset:
                    self.transcode_audio_check.setChecked(preset["transcode_audio"])
                if "corrupt_fix" in preset:
                    self.corrupt_check.setChecked(preset["corrupt_fix"])
                if "replace_files" in preset:
                    self.replace_check.setChecked(preset["replace_files"])

                # Apply text fields
                if "ffmpeg_extras" in preset:
                    self.ffmpeg_extras_edit.setText(preset["ffmpeg_extras"])
                if "avisynth_extras" in preset:
                    self.avisynth_extras_edit.setPlainText(preset["avisynth_extras"])

                QMessageBox.information(self, "Preset Loaded", f"Preset '{name}' loaded successfully!")

            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to load preset: {str(e)}")

    def save_settings(self):
        # Save current settings to QSettings
        self.settings.setValue("video_codec", self.get_selected_codec(self.video_codec_group))
        self.settings.setValue("audio_codec", self.get_selected_codec(self.audio_codec_group))
        self.settings.setValue("crf", self.crf_value.value())
        self.settings.setValue("abr", self.abr_value.value())
        self.settings.setValue("preset", self.preset_combo.currentIndex())
        self.settings.setValue("output_format", self.output_format_combo.currentIndex())

    def load_settings(self):
        # Load settings from QSettings
        if self.settings.value("crf"):
            self.crf_value.setValue(int(self.settings.value("crf", 23)))
        if self.settings.value("abr"):
            self.abr_value.setValue(int(self.settings.value("abr", 256)))
        if self.settings.value("preset"):
            self.preset_combo.setCurrentIndex(int(self.settings.value("preset", 5)))
        if self.settings.value("output_format"):
            self.output_format_combo.setCurrentIndex(int(self.settings.value("output_format", 0)))

    def closeEvent(self, event):
        self.save_settings()
        event.accept()

    def show_about(self):
        QMessageBox.about(self,
                          "About Video Processor Pro",
                          "Video Processor Pro v2.0\n\n"
                          "A professional video processing application with:\n"
                          "• Multi-format support\n"
                          "• Hardware acceleration\n"
                          "• AviSynth+ integration\n"
                          "• Batch processing\n\n"
                          "Drag and drop files or folders to process them.")

    def select_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Input Files",
            "",
            "Video Files (*.mp4 *.avi *.mkv *.mov *.wmv *.flv *.webm);;All Files (*.*)")

        if files:
            self.add_files(files)

    def add_files(self, file_paths):
        for path in file_paths:
            if os.path.isfile(path) and path not in self.file_queue:
                self.file_queue.append(path)
                item = QListWidgetItem(os.path.basename(path))
                item.setToolTip(path)

                # Add file size to item
                file_size = os.path.getsize(path) / (1024 * 1024)  # MB
                item.setText(f"{os.path.basename(path)} ({file_size:.1f} MB)")

                self.file_list.addItem(item)

        self.update_ui_state()
        self.file_count_label.setText(f"{len(self.file_queue)} files in queue")

    def update_ui_state(self):
        has_files = len(self.file_queue) > 0
        self.run_button.setEnabled(has_files)
        self.remove_files_btn.setEnabled(has_files)
        self.clear_files_btn.setEnabled(has_files)

    def get_selected_codec(self, button_group):
        selected = button_group.checkedButton()
        return selected.property("value") if selected else None

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

    @Slot()
    def run_processing(self):
        if not self.file_queue:
            return

        self.process_thread = ProcessThread(self.file_queue, self)
        self.process_thread.progress_signal.connect(self.update_progress)
        self.process_thread.info_signal.connect(self.update_info)
        self.process_thread.finished_signal.connect(self.processing_finished)
        self.process_thread.file_started_signal.connect(self.file_started)
        self.process_thread.file_finished_signal.connect(self.file_finished)
        self.process_thread.time_remaining_signal.connect(self.update_time_remaining)

        self.process_thread.start()

        # Update UI state
        self.run_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.add_files_btn.setEnabled(False)
        self.add_folder_btn.setEnabled(False)
        self.remove_files_btn.setEnabled(False)
        self.clear_files_btn.setEnabled(False)
        self.tabs.setEnabled(False)

        self.progress_bar.setMaximum(len(self.file_queue))
        self.progress_bar.setValue(0)

    @Slot()
    def stop_processing(self):
        if self.process_thread and self.process_thread.isRunning():
            self.process_thread.should_stop = True
            self.process_thread.stop_process()

            self.status_label.setText("Processing stopped")
            self.time_remaining_label.setText("")
            self.run_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.add_files_btn.setEnabled(True)
            self.add_folder_btn.setEnabled(True)
            self.remove_files_btn.setEnabled(True)
            self.clear_files_btn.setEnabled(True)
            self.tabs.setEnabled(True)

    @Slot(str, int)
    def update_progress(self, message, progress):
        self.status_label.setText(message)
        if progress > 0:
            self.progress_bar.setValue(progress)

    @Slot(str)
    def update_time_remaining(self, time_str):
        self.time_remaining_label.setText(time_str)

    @Slot(str)
    def update_info(self, message):
        print(message)  # You might want to add a log window instead

    @Slot(int)
    def file_started(self, index):
        item = self.file_list.item(index)
        if item:
            item.setBackground(Qt.GlobalColor.yellow)

    @Slot(int, bool)
    def file_finished(self, index, success):
        item = self.file_list.item(index)
        if item:
            item.setBackground(Qt.GlobalColor.green if success else Qt.GlobalColor.red)
        self.progress_bar.setValue(self.progress_bar.value() + 1)

    @Slot()
    def processing_finished(self):
        self.run_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.add_files_btn.setEnabled(True)
        self.add_folder_btn.setEnabled(True)
        self.remove_files_btn.setEnabled(True)
        self.clear_files_btn.setEnabled(True)
        self.tabs.setEnabled(True)
        self.status_label.setText("Processing complete")
        self.time_remaining_label.setText("")

        # Show completion notification
        QMessageBox.information(self, "Processing Complete",
                                f"Successfully processed {self.progress_bar.value()} files!")

    def remove_selected_files(self):
        selected_items = self.file_list.selectedItems()
        for item in selected_items:
            row = self.file_list.row(item)
            self.file_list.takeItem(row)
            self.file_queue.pop(row)

        self.update_ui_state()
        self.file_count_label.setText(f"{len(self.file_queue)} files in queue")

    def clear_queue(self):
        self.file_list.clear()
        self.file_queue.clear()
        self.update_ui_state()
        self.file_count_label.setText("0 files in queue")


if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Set application style
    app.setStyle('Fusion')

    # Optional: Set dark theme
    # dark_palette = QPalette()
    # dark_palette.setColor(QPalette.Window, QColor(53, 53, 53))
    # dark_palette.setColor(QPalette.WindowText, Qt.white)
    # dark_palette.setColor(QPalette.Base, QColor(25, 25, 25))
    # dark_palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
    # dark_palette.setColor(QPalette.ToolTipBase, Qt.black)
    # dark_palette.setColor(QPalette.ToolTipText, Qt.white)
    # dark_palette.setColor(QPalette.Text, Qt.white)
    # dark_palette.setColor(QPalette.Button, QColor(53, 53, 53))
    # dark_palette.setColor(QPalette.ButtonText, Qt.white)
    # dark_palette.setColor(QPalette.BrightText, Qt.red)
    # dark_palette.setColor(QPalette.Link, QColor(42, 130, 218))
    # dark_palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    # dark_palette.setColor(QPalette.HighlightedText, Qt.black)
    # app.setPalette(dark_palette)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())