"""
Getting an FFmpeg that can do the job.

videer measures quality with `libvmaf`, and the FFmpeg most people already have cannot: the standard Windows
"essentials" build ships without it, and so do many distribution packages. Nothing about that is visible from
inside the app — the metric simply is not there — so this module finds out what the FFmpeg in use is missing
and, where a suitable build exists, fetches one and puts it beside the application.

The builds come from BtbN/FFmpeg-Builds, which is where FFmpeg's own download page sends Windows and Linux
users. They are GPL builds with libvmaf compiled in. Nothing is downloaded without being asked for, and the
exact URL is on screen before anything starts.
"""

import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
                               QPushButton, QVBoxLayout)
from modules.widgets import WrapLabel

from config import DEFAULT_QUALITY_METRIC
from utils.ffmpeg_utils import (MANAGED_BIN_DIR, ffmpeg_has_encoder, ffmpeg_has_filter, find_ffmpeg,
                                find_ffprobe, forget_ffmpeg)

BUILD_BASE = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"

# Which build belongs to which machine. macOS is absent on purpose: BtbN does not build for it, and pointing
# an unattended download at a less established source is not a favour to anyone.
BUILDS = {
    ('Windows', 'amd64'): ("ffmpeg-master-latest-win64-gpl.zip", "zip", "Windows x64", 163),
    ('Windows', 'x86_64'): ("ffmpeg-master-latest-win64-gpl.zip", "zip", "Windows x64", 163),
    ('Windows', 'arm64'): ("ffmpeg-master-latest-winarm64-gpl.zip", "zip", "Windows ARM64", 140),
    ('Linux', 'x86_64'): ("ffmpeg-master-latest-linux64-gpl.tar.xz", "tar", "Linux x64", 123),
    ('Linux', 'amd64'): ("ffmpeg-master-latest-linux64-gpl.tar.xz", "tar", "Linux x64", 123),
    ('Linux', 'aarch64'): ("ffmpeg-master-latest-linuxarm64-gpl.tar.xz", "tar", "Linux ARM64", 105),
    ('Linux', 'arm64'): ("ffmpeg-master-latest-linuxarm64-gpl.tar.xz", "tar", "Linux ARM64", 105),
}

# What videer wants from FFmpeg beyond the basics, and what is lost without it
WANTED_FILTERS = {
    'libvmaf': "VMAF, VMAF NEG, VMAF 4K and MS-SSIM quality measurement",
    'xpsnr': "XPSNR quality measurement",
}
# Encoders that many builds leave out. VVenC is the newest of them: FFmpeg has had it since 7.1, and the
# distribution packages built from 6.x have never heard of it.
WANTED_ENCODERS = {
    'libvvenc': "H.266/VVC encoding",
    'libsvtav1': "AV1 encoding with SVT-AV1",
    'libaom-av1': "AV1 encoding with libaom",
}

MACOS_ADVICE = ("On macOS the usual source is Homebrew: `brew install ffmpeg` installs a build with libvmaf. "
                "videer will use it as soon as it is on the PATH.")


def available_build() -> Optional[Dict[str, Any]]:
    """The build for this machine, or None where there is no source worth pointing at"""
    key = (platform.system(), platform.machine().lower())
    entry = BUILDS.get(key)
    if not entry:
        return None
    name, kind, label, megabytes = entry
    return {'url': BUILD_BASE + name, 'name': name, 'kind': kind, 'label': label, 'size_mb': megabytes}


def missing_features() -> List[str]:
    """What the FFmpeg in use cannot do, in the user's terms. Empty when there is nothing to fix."""
    if not find_ffmpeg():
        return ["FFmpeg itself was not found"]
    missing = [f"{purpose} (needs the {name} filter)"
               for name, purpose in WANTED_FILTERS.items() if not ffmpeg_has_filter(name)]
    missing += [f"{purpose} (needs the {name} encoder)"
                for name, purpose in WANTED_ENCODERS.items() if not ffmpeg_has_encoder(name)]
    if not find_ffprobe():
        missing.append("reading durations and stream layouts (needs ffprobe beside ffmpeg)")
    return missing


def managed_build_installed() -> bool:
    """Whether the copy this module installed is the one in use"""
    current = find_ffmpeg()
    return bool(current and os.path.dirname(os.path.abspath(current)) == MANAGED_BIN_DIR)


# ----------------------------------------------------------------------
# The work itself, free of Qt so it can be tested and driven from anywhere
# ----------------------------------------------------------------------
def download(url: str, target: str,
             on_progress: Optional[Callable[[int, int], None]] = None,
             should_stop: Optional[Callable[[], bool]] = None) -> None:
    """Fetch a file, reporting bytes as they arrive. Raises on anything that goes wrong."""
    request = urllib.request.Request(url, headers={'User-Agent': 'videer'})
    with urllib.request.urlopen(request, timeout=60) as response, open(target, 'wb') as handle:
        total = int(response.headers.get('Content-Length') or 0)
        done = 0
        while True:
            if should_stop and should_stop():
                raise InterruptedError("cancelled")
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done, total)


def extract_binaries(archive: str, kind: str, destination: str) -> List[str]:
    """
    Pull just ffmpeg and ffprobe out of the archive — the rest of a build is documentation, presets and
    libraries videer does not use, and a few hundred megabytes nobody asked to keep.
    """
    os.makedirs(destination, exist_ok=True)
    wanted = ('ffmpeg', 'ffmpeg.exe', 'ffprobe', 'ffprobe.exe')
    written: List[str] = []

    def take(name: str, reader) -> None:
        base = os.path.basename(name)
        if base not in wanted:
            return
        path = os.path.join(destination, base)
        with open(path, 'wb') as out:
            shutil.copyfileobj(reader, out)
        os.chmod(path, 0o755)
        written.append(path)

    if kind == 'zip':
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.namelist():
                if member.endswith('/') or os.path.basename(member) not in wanted:
                    continue
                with bundle.open(member) as reader:
                    take(member, reader)
    else:
        with tarfile.open(archive, 'r:*') as bundle:
            for member in bundle.getmembers():
                if not member.isfile() or os.path.basename(member.name) not in wanted:
                    continue
                reader = bundle.extractfile(member)
                if reader:
                    take(member.name, reader)
    return written


def verify(binary: str) -> Optional[str]:
    """The version line of a binary that runs, or None if it does not"""
    try:
        result = subprocess.run([binary, '-hide_banner', '-version'],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, timeout=30)
    except (subprocess.SubprocessError, OSError):
        return None
    first = (result.stdout or '').splitlines()
    return first[0].strip() if result.returncode == 0 and first else None


class FFmpegInstaller(QThread):
    """Download and install a build, off the GUI thread"""

    progress = Signal(str)
    bytes_done = Signal(int, int)
    finished_ok = Signal(str)          # the version line of what was installed
    failed = Signal(str)

    def __init__(self, build: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.build = build
        self.should_stop = False

    def stop(self):
        self.should_stop = True

    def run(self):
        workdir = tempfile.mkdtemp(prefix="videer-ffmpeg-")
        archive = os.path.join(workdir, self.build['name'])
        try:
            self.progress.emit(f"Downloading {self.build['name']}…")
            download(self.build['url'], archive,
                     on_progress=lambda done, total: self.bytes_done.emit(done, total),
                     should_stop=lambda: self.should_stop)

            self.progress.emit("Unpacking ffmpeg and ffprobe…")
            written = extract_binaries(archive, self.build['kind'], MANAGED_BIN_DIR)
            if not any(os.path.basename(path).startswith('ffmpeg') for path in written):
                self.failed.emit("The archive downloaded, but no ffmpeg binary was found inside it.")
                return

            forget_ffmpeg()               # the newly installed copy is the one to use from here on
            binary = find_ffmpeg()
            version = verify(binary) if binary else None
            if not version:
                self.failed.emit("The downloaded ffmpeg would not run on this machine.")
                return
            if not ffmpeg_has_filter('libvmaf'):
                self.failed.emit(f"{version} installed, but it has no libvmaf filter — "
                                 f"which is the reason for fetching it.")
                return
            self.finished_ok.emit(version)
        except InterruptedError:
            self.failed.emit("Cancelled.")
        except (urllib.error.URLError, OSError, zipfile.BadZipFile, tarfile.TarError) as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


class FFmpegSetupDialog(QDialog):
    """
    What this FFmpeg cannot do, and the offer to fetch one that can.

    Deliberately explicit: the URL it will fetch, how large it is, and where it will be put. An application
    that downloads and runs a binary should say exactly which one before it does it.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("FFmpeg Features")
        self.setMinimumWidth(600)
        self.installer: Optional[FFmpegInstaller] = None
        self.build = available_build()

        layout = QVBoxLayout(self)

        self.summary = WrapLabel()
        layout.addWidget(self.summary)

        self.detail = WrapLabel()
        self.detail.setStyleSheet("color: #555; font-size: 11px;")
        layout.addWidget(self.detail)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status = WrapLabel()
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.get_button = QPushButton("Download && Install")
        self.get_button.clicked.connect(self._on_install)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._on_cancel)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(lambda: self.done(0))
        buttons.addStretch()
        buttons.addWidget(self.get_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.cancel_button.setEnabled(False)
        self._describe()

    # ------------------------------------------------------------------
    def _describe(self):
        missing = missing_features()
        current = find_ffmpeg()
        version = verify(current) if current else None

        if not missing:
            self.summary.setText("This FFmpeg can do everything videer asks of it.")
            self.detail.setText(f"{version}\n{current}" if version else "")
            self.get_button.setText("Install a newer build anyway")
        else:
            self.summary.setText(
                "This FFmpeg is missing what videer needs for:\n  • " + "\n  • ".join(missing))
            self.detail.setText(
                (f"In use: {version}\n{current}\n\n" if version else "No FFmpeg found on this machine.\n\n")
                + "VMAF is not part of a plain FFmpeg build; it has to be compiled in. The Windows "
                  "'essentials' build and many distribution packages leave it out, and the same goes for "
                  "the VVenC encoder behind H.266/VVC.")

        if not self.build:
            self.get_button.setEnabled(False)
            self.status.setText(
                "There is no automatic download for this platform. " + MACOS_ADVICE
                if platform.system() == 'Darwin' else
                "There is no automatic download for this platform — install an FFmpeg built with libvmaf and "
                "put it on the PATH, or beside videer.")
            return

        self.status.setText(
            f"Will download the {self.build['label']} build (about {self.build['size_mb']} MB) from\n"
            f"{self.build['url']}\n"
            f"and keep ffmpeg and ffprobe in {MANAGED_BIN_DIR}, which videer uses in preference to any "
            f"other. They are static builds and take about 300 MB on disk once unpacked; delete that folder "
            f"to go back to the system FFmpeg.")

    # ------------------------------------------------------------------
    def _on_install(self):
        if not self.build:
            return
        self.get_button.setEnabled(False)
        self.close_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.installer = FFmpegInstaller(self.build, parent=self)
        self.installer.progress.connect(self.status.setText)
        self.installer.bytes_done.connect(self._on_bytes)
        self.installer.finished_ok.connect(self._on_done)
        self.installer.failed.connect(self._on_failed)
        self.installer.start()

    def _on_bytes(self, done: int, total: int):
        if total > 0:
            self.progress_bar.setMaximum(100)
            self.progress_bar.setValue(int(done * 100 / total))
            self.progress_bar.setFormat(f"%p%  ({done / 1048576:.0f} of {total / 1048576:.0f} MB)")
        else:
            self.progress_bar.setMaximum(0)

    def _on_cancel(self):
        if self.installer and self.installer.isRunning():
            self.status.setText("Cancelling…")
            self.installer.stop()

    def _on_done(self, version: str):
        self.progress_bar.setValue(100)
        self.cancel_button.setEnabled(False)
        self.close_button.setEnabled(True)
        self.get_button.setEnabled(True)
        self.status.setText(f"Installed: {version}\nVMAF, every other metric and every encoder are available now.")
        self._describe_after()

    def _on_failed(self, message: str):
        self.progress_bar.setVisible(False)
        self.cancel_button.setEnabled(False)
        self.close_button.setEnabled(True)
        self.get_button.setEnabled(True)
        self.status.setText(f"Did not install — {message}")

    def _describe_after(self):
        """
        Re-read what FFmpeg can do. The metric asked for by default is selected outright: whoever just waited
        for a 123 MB download did it to be able to measure VMAF, not to be left on the stand-in.
        """
        parent = self.parent()
        refresh = getattr(getattr(parent, 'ui_manager', None), 'refresh_metric_choices', None)
        if callable(refresh):
            refresh(DEFAULT_QUALITY_METRIC)
        self._describe()

    def closeEvent(self, event):
        if self.installer and self.installer.isRunning():
            self.installer.stop()
            self.installer.wait(3000)
        super().closeEvent(event)


DECLINED_KEY = "ffmpeg_setup_declined"


def offer_if_incomplete(main_window, qsettings=None) -> None:
    """
    Ask at startup when the FFmpeg in use cannot do what videer is for, and take "no" for an answer — an
    application that nags every launch about a dependency it is already running on is just noise. Silent when
    nothing is missing, and silent again once declined, since Tools > FFmpeg Features is always there.
    """
    missing = missing_features()
    if not missing:
        return

    have_ffmpeg = bool(find_ffmpeg())
    if have_ffmpeg and not available_build():
        return          # nothing to offer, so nothing to ask
    if have_ffmpeg and qsettings is not None and str(qsettings.value(DECLINED_KEY, "")).lower() == 'true':
        return          # already said no once; the menu entry is enough

    if not have_ffmpeg:
        title = "FFmpeg Not Found"
        question = ("FFmpeg was not found on this machine, and videer cannot encode anything without it."
                    "\n\nFetch a build now?")
    else:
        title = "FFmpeg Is Missing Features"
        question = ("This FFmpeg cannot do:\n  \u2022 " + "\n  \u2022 ".join(missing)
                    + "\n\nFetch a build that can?")

    answer = QMessageBox.question(main_window, title, question,
                                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    if answer == QMessageBox.StandardButton.Yes:
        FFmpegSetupDialog(main_window).exec()
    elif qsettings is not None:
        qsettings.setValue(DECLINED_KEY, True)
