#!/usr/bin/env python3
"""
Build a self-contained videer distribution (no Python required on the target) using PyInstaller,
then package it as
    dist/videer-<version>-<os>-<arch>.zip      (Windows)
    dist/videer-<version>-<os>-<arch>.tar.gz   (Linux / macOS, keeps exec bits)

Usage:  python build.py            (run from the repository root)
Needs:  pip install -r requirements.txt pyinstaller
"""
import glob
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from config import APP_NAME, APP_VERSION  # noqa: E402

BUILD_DIR = os.path.join(ROOT, "build")
DIST_DIR = os.path.join(ROOT, "dist")
EXTRA_FILES = ("README.md", "LICENSE", "icon.ico")


def os_tag() -> str:
    return {"windows": "windows", "linux": "linux", "darwin": "macos"}.get(
        platform.system().lower(), platform.system().lower())


def arch_tag() -> str:
    machine = platform.machine().lower()
    return {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)


def pyinstaller_command() -> list:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir", "--windowed",
        f"--name={APP_NAME}",
        f"--distpath={os.path.join(BUILD_DIR, 'out')}",
        f"--workpath={os.path.join(BUILD_DIR, 'work')}",
        f"--specpath={BUILD_DIR}",
        # trim Qt modules we never use
        "--exclude-module=PySide6.QtWebEngineCore", "--exclude-module=PySide6.QtWebEngineWidgets",
        "--exclude-module=PySide6.Qt3DCore", "--exclude-module=PySide6.QtQuick", "--exclude-module=PySide6.QtQml",
        "--exclude-module=PySide6.QtMultimedia", "--exclude-module=PySide6.QtCharts", "--exclude-module=PySide6.QtPdf",
        "--exclude-module=torch", "--exclude-module=tensorflow", "--exclude-module=tkinter",
        f"--add-data={os.path.join(ROOT, 'icon.ico')}{os.pathsep}.",
        f"--add-data={os.path.join(ROOT, 'plugins')}{os.pathsep}plugins",
        "--copy-metadata=psutil",
    ]
    if platform.system() != "Darwin":
        cmd.append(f"--icon={os.path.join(ROOT, 'icon.ico')}")
    cmd.append(os.path.join(ROOT, "main.py"))
    return cmd


def find_output_dir() -> str:
    out = os.path.join(BUILD_DIR, "out")
    if platform.system() == "Darwin":
        app = os.path.join(out, f"{APP_NAME}.app")
        if os.path.isdir(app):
            return app
    folder = os.path.join(out, APP_NAME)
    if os.path.isdir(folder):
        return folder
    raise SystemExit("PyInstaller output directory not found in build/out")


def package(out_dir: str) -> str:
    os.makedirs(DIST_DIR, exist_ok=True)
    stem = f"{APP_NAME}-{APP_VERSION}-{os_tag()}-{arch_tag()}"
    for name in EXTRA_FILES:
        src = os.path.join(ROOT, name)
        if os.path.exists(src):
            dest_root = os.path.join(out_dir, "Contents", "MacOS") if out_dir.endswith(".app") else out_dir
            shutil.copy2(src, os.path.join(dest_root, name))
    os.makedirs(os.path.join(out_dir if not out_dir.endswith(".app") else os.path.join(out_dir, "Contents", "MacOS"),
                             "presets"), exist_ok=True)
    top_name = f"{APP_NAME}.app" if out_dir.endswith(".app") else stem
    if platform.system() == "Windows":
        archive = os.path.join(DIST_DIR, stem + ".zip")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for folder, _dirs, files in os.walk(out_dir):
                for fn in files:
                    full = os.path.join(folder, fn)
                    zf.write(full, os.path.join(top_name, os.path.relpath(full, out_dir)))
    else:
        archive = os.path.join(DIST_DIR, stem + ".tar.gz")
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(out_dir, arcname=top_name)
    return archive


def main():
    os.chdir(ROOT)
    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    cmd = pyinstaller_command()
    print("Running:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)
    archive = package(find_output_dir())
    print(f"\nBuilt {archive} ({os.path.getsize(archive) / (1024 * 1024):.1f} MB)")


if __name__ == "__main__":
    main()
