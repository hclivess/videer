"""
Getting VapourSynth for the QTempGaussMC deinterlacer, from inside the app.

VapourSynth, vs-jetpack and the plugins QTempGaussMC calls are all published as Python wheels, so what a machine
needs is a Python to install them into — and a frozen videer has none, while a Python the user happens to have
is theirs to keep tidy. So videer fetches its own: a pinned, checksummed python-build-standalone build (the
relocatable CPython that uv and others ship), unpacked into DATA_DIR/vapoursynth/python, with
requirements-vapoursynth.txt installed into it by its own pip. Nothing outside that folder is touched — no
registry, no VapourSynth configuration file, no PATH — and deleting the folder undoes it.

pip's console scripts carry an absolute path to their interpreter, so the environment cannot be built elsewhere
and moved into place. It is built where it stays, and a marker written as the very last step is what makes it
count: find_vspipe() ignores the folder until the marker is there, so an interrupted install is never used.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.error
from typing import Callable, Dict, Any, Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QProgressBar, QPushButton, QVBoxLayout, QPlainTextEdit

from modules.widgets import WrapLabel
from modules.frameservers.vapoursynth import (MANAGED_CONFIG_HOME, MANAGED_MARKER as MARKER, MANAGED_PYTHON_HOME,
                                              MANAGED_VS_DIR, check_environment, find_vspipe, forget_checks,
                                              managed_vspipe, vspipe_env, vspipe_in)
from utils import childproc
from utils.download import download, sha256_of

PYTHON_RELEASE = "20261021"
PYTHON_VERSION = "3.12.15"
PYTHON_BASE = "https://github.com/astral-sh/python-build-standalone/releases/download/"

# (platform.system(), platform.machine().lower()) -> (target triple, sha256 of the archive, size in MB).
# Pinned and checked: an archive that does not match is refused before anything in it runs. Windows on ARM is
# absent because VapourSynth publishes no wheel for it.
PYTHON_BUILDS = {
    ('Windows', 'amd64'): ("x86_64-pc-windows-msvc",
                           "aac5d331fc3307343f95928fdbea4583f0d53973d08c7e1d081884fbd229a0e1", 21),
    ('Linux', 'x86_64'): ("x86_64-unknown-linux-gnu",
                          "6d7521138fd33afddc1ccd08abb007d9a2829e38117c2576f9efdc0ec73ce772", 33),
    ('Linux', 'aarch64'): ("aarch64-unknown-linux-gnu",
                           "b4567aa5a11f51b540c4dfd9e082ebf23e554f8ac17b85caa6cea23e83693394", 28),
    ('Darwin', 'arm64'): ("aarch64-apple-darwin",
                          "7b8d00b751db21cea87dc551a8f8165147c5fbc5251d5537d7e5497dde00a219", 24),
    ('Darwin', 'x86_64'): ("x86_64-apple-darwin",
                           "f12e0504397683d6be0745ffd4c39ef45a455cec169adf35c628840d06133773", 24),
}
PYTHON_BUILDS[('Windows', 'x86_64')] = PYTHON_BUILDS[('Windows', 'amd64')]
PYTHON_BUILDS[('Linux', 'arm64')] = PYTHON_BUILDS[('Linux', 'aarch64')]

# What the requirements and the environment cost on disk, for the dialog to say up front
INSTALLED_SIZE_MB = 250

REQUIREMENTS_NAME = "requirements-vapoursynth.txt"


class InstallError(Exception):
    """The install did not complete; the message says why, in the user's terms"""


def available_build() -> Optional[Dict[str, Any]]:
    """The Python build for this machine, or None where VapourSynth cannot be installed this way"""
    entry = PYTHON_BUILDS.get((platform.system(), platform.machine().lower()))
    if not entry:
        return None
    triple, sha256, megabytes = entry
    name = f"cpython-{PYTHON_VERSION}+{PYTHON_RELEASE}-{triple}-install_only_stripped.tar.gz"
    return {'name': name, 'url': f"{PYTHON_BASE}{PYTHON_RELEASE}/{name.replace('+', '%2B')}",
            'sha256': sha256, 'size_mb': megabytes}


def _python_in(home: str) -> str:
    return os.path.join(home, "python.exe") if sys.platform == 'win32' else os.path.join(home, "bin", "python3")


def requirements_file() -> str:
    """requirements-vapoursynth.txt: inside the bundle when frozen, at the repository root from source"""
    base = getattr(sys, '_MEIPASS', None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, REQUIREMENTS_NAME)


def install_vapoursynth(on_status: Callable[[str], None],
                        on_bytes: Optional[Callable[[int, int], None]] = None,
                        should_stop: Callable[[], bool] = lambda: False) -> str:
    """
    Fetch Python, install the VapourSynth stack into it, prove QTempGaussMC runs, and mark the install complete.
    Returns the path of the vspipe now in use. Raises InstallError (or InterruptedError when cancelled).
    """
    build = available_build()
    if build is None:
        raise InstallError(f"there is no VapourSynth build for {platform.system()} on {platform.machine()}")
    requirements = requirements_file()
    if not os.path.isfile(requirements):
        raise InstallError(f"{REQUIREMENTS_NAME} is missing from this copy of videer ({requirements})")

    # A previous install, complete or not, is replaced as a whole: mixing two environments is how a half-upgraded
    # one ends up running.
    shutil.rmtree(MANAGED_VS_DIR, ignore_errors=True)
    if os.path.exists(MANAGED_VS_DIR):
        raise InstallError(f"could not remove the previous install at {MANAGED_VS_DIR} — is something using it?")
    os.makedirs(MANAGED_VS_DIR)
    forget_checks()

    archive = os.path.join(MANAGED_VS_DIR, build['name'])
    try:
        on_status(f"Downloading Python {PYTHON_VERSION} ({build['size_mb']} MB)…")
        download(build['url'], archive, on_progress=on_bytes, should_stop=should_stop)
        actual = sha256_of(archive)
        if actual != build['sha256']:
            raise InstallError(f"the downloaded Python does not match its published checksum "
                               f"(expected {build['sha256']}, got {actual}) — refusing to run it")

        on_status("Unpacking Python…")
        with tarfile.open(archive, 'r:gz') as bundle:
            # The 'data' filter refuses absolute paths, links out of the folder and device files
            bundle.extractall(MANAGED_VS_DIR, filter='data')
    except urllib.error.URLError as exc:
        raise InstallError(f"download failed: {exc}") from exc
    except (OSError, tarfile.TarError) as exc:
        raise InstallError(f"unpacking failed: {exc}") from exc
    finally:
        try:
            os.remove(archive)
        except OSError:
            pass

    home = MANAGED_PYTHON_HOME
    python = _python_in(home)
    if not os.path.isfile(python):
        raise InstallError(f"the Python archive did not contain {os.path.relpath(python, MANAGED_VS_DIR)}")

    on_status("Installing VapourSynth, vs-jetpack and the QTempGaussMC plugins…")
    _pip_install(python, requirements, on_status, should_stop)

    vspipe = vspipe_in(home)
    if not os.path.isfile(vspipe):
        raise InstallError("VapourSynth installed, but it brought no vspipe")
    if sys.platform != 'win32':
        _configure_vsscript(python, vspipe)

    on_status("Checking that QTempGaussMC runs…")
    problem = check_environment(vspipe)
    if problem:
        raise InstallError(f"VapourSynth installed, but QTempGaussMC does not run: {problem}")

    with open(MARKER + ".tmp", 'w', encoding='utf-8') as handle:
        json.dump({'python': PYTHON_VERSION, 'python_release': PYTHON_RELEASE,
                   'requirements': open(requirements, encoding='utf-8').read()}, handle, indent=1)
    os.replace(MARKER + ".tmp", MARKER)
    forget_checks()
    return vspipe


def _pip_install(python: str, requirements: str, on_status: Callable[[str], None],
                 should_stop: Callable[[], bool]) -> None:
    command = [python, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-warn-script-location',
               '--only-binary=:all:', '-r', requirements]
    try:
        process = childproc.popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace')
    except OSError as exc:
        raise InstallError(f"could not run pip: {exc}") from exc
    tail = []
    try:
        for line in process.stdout:
            line = line.rstrip()
            if should_stop():
                raise InterruptedError("cancelled")
            if line:
                tail = (tail + [line])[-8:]
                if line.startswith(('Collecting', 'Downloading', 'Installing', 'Successfully')):
                    on_status(line)
        code = process.wait()
    finally:
        childproc.release(process)
    if code != 0:
        raise InstallError("pip could not install the VapourSynth packages:\n" + "\n".join(tail))


def _configure_vsscript(python: str, vspipe: str) -> None:
    """
    Tell VSScript which Python to embed. `vapoursynth config` writes vapoursynth.toml under $XDG_CONFIG_HOME, which
    for this install is a folder inside it (see MANAGED_CONFIG_HOME) — the user's own configuration is not touched.
    """
    os.makedirs(MANAGED_CONFIG_HOME, exist_ok=True)
    try:
        result = childproc.run([python, '-m', 'vapoursynth', 'config'], text=True, encoding='utf-8',
                               errors='replace', timeout=120, env=vspipe_env(vspipe))
    except (OSError, subprocess.SubprocessError) as exc:
        raise InstallError(f"could not configure VapourSynth: {exc}") from exc
    if result.returncode != 0 or 'successfully' not in (result.stdout or '').lower():
        output = ((result.stdout or '') + (result.stderr or '')).strip()
        raise InstallError(f"could not configure VapourSynth: {output or f'exit code {result.returncode}'}")


def uninstall_vapoursynth() -> None:
    shutil.rmtree(MANAGED_VS_DIR, ignore_errors=True)
    forget_checks()


# ----------------------------------------------------------------------
# Qt
# ----------------------------------------------------------------------
class VapourSynthInstaller(QThread):
    """install_vapoursynth() off the GUI thread"""

    progress = Signal(str)
    bytes_done = Signal(int, int)
    finished_ok = Signal(str)          # the vspipe now in use
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.should_stop = False

    def stop(self):
        self.should_stop = True

    def run(self):
        try:
            vspipe = install_vapoursynth(self.progress.emit,
                                         lambda done, total: self.bytes_done.emit(done, total),
                                         lambda: self.should_stop)
            self.finished_ok.emit(vspipe)
        except InterruptedError:
            uninstall_vapoursynth()
            self.failed.emit("Cancelled.")
        except InstallError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:                          # noqa: BLE001 - reported, never swallowed
            self.failed.emit(f"{type(exc).__name__}: {exc}")


def describe_status() -> str:
    """One line for the Processing tab: is QTempGaussMC usable here, and with which vspipe"""
    vspipe = find_vspipe()
    if not vspipe:
        if available_build() is None:
            return "QTempGaussMC: not available on this platform (VapourSynth has no build for it)."
        return "QTempGaussMC: VapourSynth is not installed."
    problem = check_environment(vspipe)
    if problem:
        return f"QTempGaussMC: VapourSynth found but cannot run it — {problem}"
    origin = "installed by videer" if vspipe == managed_vspipe() else "found"
    return f"QTempGaussMC: ready — VapourSynth {origin} ({vspipe})"


class StatusCheck(QThread):
    """describe_status() off the GUI thread: the check runs QTempGaussMC once, which takes a few seconds"""

    done = Signal(str)

    def run(self):
        try:
            self.done.emit(describe_status())
        except Exception as exc:                          # noqa: BLE001 - shown, never swallowed
            self.done.emit(f"QTempGaussMC: could not check VapourSynth — {type(exc).__name__}: {exc}")


class VapourSynthSetupDialog(QDialog):
    """
    The state of the QTempGaussMC backend, and the offer to install it. Says exactly what will be fetched from
    where and where it goes before anything starts.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("VapourSynth for QTempGaussMC")
        self.setMinimumWidth(620)
        self.installer: Optional[VapourSynthInstaller] = None
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
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)
        self.log.setVisible(False)
        layout.addWidget(self.log)

        buttons = QHBoxLayout()
        self.install_button = QPushButton("Download && Install")
        self.install_button.clicked.connect(self._on_install)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self._on_remove)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._on_cancel)
        self.cancel_button.setEnabled(False)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(lambda: self.done(0))
        buttons.addStretch()
        for button in (self.install_button, self.remove_button, self.cancel_button, self.close_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self._describe()

    def _describe(self):
        self.summary.setText(describe_status())
        self.remove_button.setEnabled(os.path.isdir(MANAGED_VS_DIR))
        if not self.build:
            self.install_button.setEnabled(False)
            self.detail.setText("VapourSynth publishes no build for this machine, so QTempGaussMC cannot run "
                                "here. QTGMC (AviSynth+) and bwdif remain available.")
            return
        self.install_button.setText("Reinstall" if managed_vspipe() else "Download && Install")
        self.detail.setText(
            f"Downloads Python {PYTHON_VERSION} (about {self.build['size_mb']} MB, checked against its published "
            f"SHA-256) from\n{self.build['url']}\nand installs VapourSynth, vs-jetpack and the plugins "
            f"QTempGaussMC uses into it from PyPI — about {INSTALLED_SIZE_MB} MB in all, kept in\n"
            f"{MANAGED_VS_DIR}\nNothing else on the machine is changed; Remove, or deleting that folder, undoes it.")

    def _busy(self, busy: bool):
        self.install_button.setEnabled(not busy and bool(self.build))
        self.remove_button.setEnabled(not busy and os.path.isdir(MANAGED_VS_DIR))
        self.close_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    def _on_install(self):
        self._busy(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.log.clear()
        self.log.setVisible(True)
        self.installer = VapourSynthInstaller(self)
        self.installer.progress.connect(self._on_progress)
        self.installer.bytes_done.connect(self._on_bytes)
        self.installer.finished_ok.connect(self._on_done)
        self.installer.failed.connect(self._on_failed)
        self.installer.start()

    def _on_progress(self, message: str):
        self.log.appendPlainText(message)
        if not message.startswith("Downloading Python"):
            self.progress_bar.setRange(0, 0)

    def _on_bytes(self, done: int, total: int):
        if total > 0:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(int(done * 100 / total))
            self.progress_bar.setFormat(f"%p%  ({done / 1048576:.0f} of {total / 1048576:.0f} MB)")

    def _on_cancel(self):
        if self.installer and self.installer.isRunning():
            self.log.appendPlainText("Cancelling…")
            self.installer.stop()

    def _on_done(self, vspipe: str):
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self.log.appendPlainText(f"Done: {vspipe}")
        self._busy(False)
        self._describe()

    def _on_failed(self, message: str):
        self.progress_bar.setVisible(False)
        self.log.appendPlainText(f"Did not install — {message}")
        self._busy(False)
        self._describe()

    def _on_remove(self):
        uninstall_vapoursynth()
        self._describe()

    def closeEvent(self, event):
        if self.installer and self.installer.isRunning():
            self.installer.stop()
            self.installer.wait(5000)
        super().closeEvent(event)
