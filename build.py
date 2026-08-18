#!/usr/bin/env python3
"""
Build a self-contained videer distribution (no Python required on the target)
using Nuitka, then package it as
    dist/videer-<version>-<os>-<arch>.zip      (Windows)
    dist/videer-<version>-<os>-<arch>.tar.gz   (Linux / macOS, keeps exec bits)

Usage:  python build.py            (run from the repository root)
Needs:  pip install -r requirements.txt nuitka
        Windows: MSVC or MinGW (Nuitka downloads MinGW when asked)
        Linux:   gcc, patchelf (pip install patchelf)
        macOS:   Xcode command-line tools
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


def nuitka_command() -> list:
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        "--enable-plugin=pyside6",
        "--noinclude-qt-translations",
        f"--output-dir={BUILD_DIR}",
        f"--output-filename={APP_NAME}",
        "--include-data-dir=plugins=plugins",
        "--include-data-files=icon.ico=icon.ico",
        f"--product-name={APP_NAME}",
        f"--product-version={APP_VERSION}",
        f"--file-version={APP_VERSION}",
        "--file-description=FFmpeg batch GUI with AviSynth+ support",
        "--copyright=MIT License",
    ]
    system = platform.system()
    if system == "Windows":
        cmd += ["--windows-console-mode=disable", "--windows-icon-from-ico=icon.ico"]
    elif system == "Darwin":
        cmd += ["--macos-create-app-bundle", f"--macos-app-name={APP_NAME}",
                f"--macos-app-version={APP_VERSION}"]
    cmd.append("main.py")
    return cmd


def find_output_dir() -> str:
    """Nuitka writes main.dist (or main.app on macOS with a bundle)"""
    for pattern in ("main.dist", "main.app", f"{APP_NAME}.app", "*.dist", "*.app"):
        matches = glob.glob(os.path.join(BUILD_DIR, pattern))
        if matches:
            return matches[0]
    raise SystemExit("Nuitka output directory not found in build/")


def package(out_dir: str) -> str:
    os.makedirs(DIST_DIR, exist_ok=True)
    stem = f"{APP_NAME}-{APP_VERSION}-{os_tag()}-{arch_tag()}"

    for name in EXTRA_FILES:
        src = os.path.join(ROOT, name)
        if os.path.exists(src):
            # inside an .app bundle the payload lives in Contents/MacOS
            dest_root = out_dir
            if out_dir.endswith(".app"):
                dest_root = os.path.join(out_dir, "Contents", "MacOS")
            shutil.copy2(src, os.path.join(dest_root, name))

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
    cmd = nuitka_command()
    print("Running:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)
    out_dir = find_output_dir()
    archive = package(out_dir)
    size_mb = os.path.getsize(archive) / (1024 * 1024)
    print(f"\nBuilt {archive} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
