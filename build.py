#!/usr/bin/env python3
"""
Build a self-contained videer distribution (no Python required on the target) using PyInstaller,
then package it as an archive and an installer for the OS it runs on:

    dist/videer-<version>-windows-<arch>.zip      + videer-<version>-windows-<arch>-setup.exe   (Inno Setup)
    dist/videer-<version>-linux-<arch>.tar.gz     + videer-<version>-linux-<arch>.deb           (dpkg-deb)
    dist/videer-<version>-macos-<arch>.tar.gz     + videer-<version>-macos-<arch>.dmg           (hdiutil)

each with a .sha256 beside it. The archive is the portable copy (unpack anywhere, everything it writes stays in
its folder); the installer puts it where the OS expects programs and gives it a menu entry.

Usage:  python build.py                       (run from the repository root)
        python build.py --no-installer        (archive only, for a quick local build)
        python build.py --checksum FILE...    (rewrite the .sha256 of files that came back signed)
Needs:  pip install -r requirements.txt pyinstaller
        Windows installer: Inno Setup 6 (ISCC.exe on PATH or in its default folder)
"""
import hashlib
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from config import APP_NAME, APP_VERSION  # noqa: E402

APP_DESCRIPTION = "FFmpeg batch encoder with QTGMC / QTempGaussMC deinterlacing and measured per-file CRF"
APP_URL = "https://github.com/hclivess/videer"
MAINTAINER = "hclivess <cyphernormie@gmail.com>"
# Inno Setup's AppId: fixed forever, it is how an upgrade finds the installation it replaces
INNO_APP_ID = "{{6B0E8E52-3C1A-4C55-9E57-1D2B5F0A7D31}"

BUILD_DIR = os.path.join(ROOT, "build")
DIST_DIR = os.path.join(ROOT, "dist")
EXTRA_FILES = ("README.md", "LICENSE", "icon.ico")


def os_tag() -> str:
    return {"windows": "windows", "linux": "linux", "darwin": "macos"}.get(
        platform.system().lower(), platform.system().lower())


def arch_tag() -> str:
    machine = platform.machine().lower()
    return {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)


def stem() -> str:
    return f"{APP_NAME}-{APP_VERSION}-{os_tag()}-{arch_tag()}"


VERSION_INFO_TEMPLATE = """\
# Windows version resource. An executable with no publisher/product metadata looks like malware to
# SmartScreen and to antivirus heuristics; this does not replace a signature, but it is the cheap half.
VSVersionInfo(
  ffi=FixedFileInfo(filevers=%(vers)s, prodvers=%(vers)s, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable("040904B0", [
      StringStruct("CompanyName", "%(author)s"),
      StringStruct("FileDescription", "%(description)s"),
      StringStruct("FileVersion", "%(version)s"),
      StringStruct("InternalName", "%(name)s"),
      StringStruct("LegalCopyright", "%(copyright)s"),
      StringStruct("OriginalFilename", "%(name)s.exe"),
      StringStruct("ProductName", "%(name)s"),
      StringStruct("ProductVersion", "%(version)s"),
    ])]),
    VarFileInfo([VarStruct("Translation", [1033, 1200])]),
  ]
)
"""


def version_tuple() -> tuple:
    parts = [int(p) for p in re.findall(r"\d+", APP_VERSION)][:4]
    return tuple(parts + [0] * (4 - len(parts)))


def copyright_and_author() -> tuple:
    copyright_line = "MIT licensed"
    try:
        with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("Copyright"):
                    copyright_line = line.strip()
                    break
    except OSError:
        pass
    # "Copyright (c) 2019 Jan Kucera" -> "Jan Kucera"
    author = re.sub(r"^.*?\)\s*|^\s*copyright\s*", "", copyright_line, flags=re.I)
    author = re.sub(r"^\s*\d{4}(\s*[-,]\s*\d{4})?\s*", "", author).strip() or APP_NAME
    return copyright_line, author


def write_version_info() -> str:
    """Windows version resource for the .exe, generated from config.py and LICENSE (returns its path)."""
    copyright_line, author = copyright_and_author()
    os.makedirs(BUILD_DIR, exist_ok=True)
    path = os.path.join(BUILD_DIR, "version_info.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(VERSION_INFO_TEMPLATE % {
            "vers": version_tuple(), "version": APP_VERSION, "name": APP_NAME, "author": author,
            "description": APP_DESCRIPTION, "copyright": copyright_line})
    return path


def pyinstaller_command() -> list:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir", "--windowed", "--noupx",
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
        # what the in-app VapourSynth install feeds to pip
        f"--add-data={os.path.join(ROOT, 'requirements-vapoursynth.txt')}{os.pathsep}.",
        "--copy-metadata=psutil",
    ]
    if platform.system() != "Darwin":
        cmd.append(f"--icon={os.path.join(ROOT, 'icon.ico')}")
    else:
        cmd.append(f"--osx-bundle-identifier=io.github.hclivess.{APP_NAME}")
    if platform.system() == "Windows":
        cmd.append(f"--version-file={write_version_info()}")
    cmd.append(os.path.join(ROOT, "main.py"))
    return cmd


def sign(path: str) -> None:
    """
    Authenticode-sign `path` when SIGN_COMMAND is set (Windows releases).

    Unsigned executables are what makes SmartScreen say "Windows protected your PC" and antivirus heuristics
    flag a fresh PyInstaller build. SIGN_COMMAND is a full command line with {file} where the path goes, e.g.
        signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /f cert.pfx /p PASS {file}
    A failing signature fails the build - a release must never go out silently unsigned.
    """
    command = os.environ.get("SIGN_COMMAND", "").strip()
    if not command:
        return
    if not os.path.exists(path):
        raise SystemExit(f"SIGN_COMMAND is set but {path} does not exist")
    print(f"Signing {path}", flush=True)      # never the command line itself: it carries the key password
    if platform.system() == "Windows":
        # let Windows parse the command line - signtool lives under "C:\Program Files (x86)\..."
        subprocess.run(command.replace("{file}", f'"{path}"'), check=True)
    else:
        subprocess.run([part.replace("{file}", path) for part in shlex.split(command)], check=True)


def checksum(path: str) -> str:
    """Write <path>.sha256 next to the file so downloads can be verified"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    out = path + ".sha256"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"{digest.hexdigest()}  {os.path.basename(path)}\n")
    return out


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


def content_root(out_dir: str) -> str:
    """Where the executable and its companions live: the folder itself, or Contents/MacOS in a .app"""
    return os.path.join(out_dir, "Contents", "MacOS") if out_dir.endswith(".app") else out_dir


def add_extras(out_dir: str) -> None:
    root = content_root(out_dir)
    for name in EXTRA_FILES:
        src = os.path.join(ROOT, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(root, name))
    os.makedirs(os.path.join(root, "presets"), exist_ok=True)


def package_archive(out_dir: str) -> str:
    os.makedirs(DIST_DIR, exist_ok=True)
    top_name = f"{APP_NAME}.app" if out_dir.endswith(".app") else stem()
    if platform.system() == "Windows":
        archive = os.path.join(DIST_DIR, stem() + ".zip")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for folder, _dirs, files in os.walk(out_dir):
                for fn in files:
                    full = os.path.join(folder, fn)
                    zf.write(full, os.path.join(top_name, os.path.relpath(full, out_dir)))
    else:
        archive = os.path.join(DIST_DIR, stem() + ".tar.gz")
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(out_dir, arcname=top_name)
    return archive


# ----------------------------------------------------------------------
# Installers
# ----------------------------------------------------------------------
INNO_TEMPLATE = r"""; Generated by build.py - edit there, not here
[Setup]
AppId=%(app_id)s
AppName=%(name)s
AppVersion=%(version)s
AppVerName=%(name)s %(version)s
AppPublisher=%(author)s
AppPublisherURL=%(url)s
AppSupportURL=%(url)s/issues
AppCopyright=%(copyright)s
; Per user, no administrator rights: videer keeps presets, the queue and the FFmpeg / VapourSynth it downloads
; beside itself, so the folder it is installed in has to be the user's own.
PrivilegesRequired=lowest
DefaultDirName={userpf}\%(name)s
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputDir=%(dist)s
OutputBaseFilename=%(output)s
SetupIconFile=%(icon)s
UninstallDisplayIcon={app}\%(name)s.exe
UninstallDisplayName=%(name)s %(version)s
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=%(arch)s
ArchitecturesInstallIn64BitMode=%(arch)s
VersionInfoVersion=%(version4)s
VersionInfoDescription=%(name)s installer
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "%(source)s\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\%(name)s"; Filename: "{app}\%(name)s.exe"; AppUserModelID: "hclivess.%(name)s"
Name: "{autodesktop}\%(name)s"; Filename: "{app}\%(name)s.exe"; Tasks: desktopicon; AppUserModelID: "hclivess.%(name)s"

[Run]
Filename: "{app}\%(name)s.exe"; Description: "{cm:LaunchProgram,%(name)s}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; What videer itself put beside the program. The user's own presets and defaults.json are left alone.
Type: filesandordirs; Name: "{app}\ffmpeg"
Type: filesandordirs; Name: "{app}\vapoursynth"
Type: files; Name: "{app}\queue.json"
"""


def find_iscc() -> str:
    candidates = [shutil.which("ISCC"), shutil.which("iscc")]
    local = os.environ.get("LOCALAPPDATA")
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.path.join(local, "Programs") if local else None):
        if base:
            candidates.append(os.path.join(base, "Inno Setup 6", "ISCC.exe"))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise SystemExit("Inno Setup 6 (ISCC.exe) not found - install it, or build with --no-installer")


def windows_installer(out_dir: str) -> str:
    copyright_line, author = copyright_and_author()
    script = os.path.join(BUILD_DIR, f"{APP_NAME}.iss")
    with open(script, "w", encoding="utf-8-sig") as fh:
        fh.write(INNO_TEMPLATE % {
            "app_id": INNO_APP_ID, "name": APP_NAME, "version": APP_VERSION, "author": author,
            "url": APP_URL, "copyright": copyright_line, "dist": DIST_DIR, "output": stem() + "-setup",
            "icon": os.path.join(ROOT, "icon.ico"), "source": out_dir,
            "arch": "arm64" if arch_tag() == "arm64" else "x64compatible",
            "version4": ".".join(str(p) for p in version_tuple())})
    subprocess.run([find_iscc(), "/Q", script], check=True)
    installer = os.path.join(DIST_DIR, stem() + "-setup.exe")
    if not os.path.isfile(installer):
        raise SystemExit(f"Inno Setup reported success but {installer} is missing")
    sign(installer)
    return installer


DESKTOP_ENTRY = """\
[Desktop Entry]
Type=Application
Name=videer
GenericName=Video Encoder
Comment=%(description)s
Exec=videer %%F
Icon=videer
Terminal=false
Categories=AudioVideo;Video;AudioVideoEditing;
MimeType=video/mp4;video/x-matroska;video/x-msvideo;video/quicktime;video/webm;video/mpeg;video/mp2t;
StartupWMClass=videer
"""

DEB_CONTROL = """\
Package: videer
Version: %(version)s
Section: video
Priority: optional
Architecture: %(arch)s
Maintainer: %(maintainer)s
Homepage: %(url)s
Depends: libgl1, libegl1, libxkbcommon0, libdbus-1-3, libfontconfig1
Recommends: ffmpeg
Description: %(description)s
 FFmpeg batch GUI: queue files, pick an encoder, and let videer measure the CRF each file
 needs. Deinterlaces with QTempGaussMC (VapourSynth, installed from inside the app),
 bwdif or yadif. Self-contained: brings its own Python and Qt.
"""


def linux_installer(out_dir: str) -> str:
    root = os.path.join(BUILD_DIR, "deb")
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(out_dir, os.path.join(root, "opt", APP_NAME), symlinks=True)
    os.makedirs(os.path.join(root, "usr", "bin"))
    os.symlink(f"/opt/{APP_NAME}/{APP_NAME}", os.path.join(root, "usr", "bin", APP_NAME))
    apps = os.path.join(root, "usr", "share", "applications")
    os.makedirs(apps)
    with open(os.path.join(apps, f"{APP_NAME}.desktop"), "w", encoding="utf-8") as fh:
        fh.write(DESKTOP_ENTRY % {"description": APP_DESCRIPTION})
    icons = os.path.join(root, "usr", "share", "icons", "hicolor", "512x512", "apps")
    os.makedirs(icons)
    shutil.copy2(os.path.join(ROOT, "icon.png"), os.path.join(icons, f"{APP_NAME}.png"))
    os.makedirs(os.path.join(root, "DEBIAN"))
    with open(os.path.join(root, "DEBIAN", "control"), "w", encoding="utf-8") as fh:
        fh.write(DEB_CONTROL % {"version": APP_VERSION, "maintainer": MAINTAINER, "url": APP_URL,
                                "description": APP_DESCRIPTION,
                                "arch": {"x64": "amd64", "arm64": "arm64"}.get(arch_tag(), arch_tag())})
    for folder, dirs, _files in os.walk(root):
        for name in dirs:
            os.chmod(os.path.join(folder, name), 0o755)
    os.makedirs(DIST_DIR, exist_ok=True)
    package = os.path.join(DIST_DIR, stem() + ".deb")
    subprocess.run(["dpkg-deb", "--build", "--root-owner-group", root, package], check=True)
    return package


def macos_installer(out_dir: str) -> str:
    staging = os.path.join(BUILD_DIR, "dmg")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    shutil.copytree(out_dir, os.path.join(staging, os.path.basename(out_dir)), symlinks=True)
    os.symlink("/Applications", os.path.join(staging, "Applications"))
    os.makedirs(DIST_DIR, exist_ok=True)
    image = os.path.join(DIST_DIR, stem() + ".dmg")
    subprocess.run(["hdiutil", "create", "-volname", f"{APP_NAME} {APP_VERSION}", "-srcfolder", staging,
                    "-ov", "-format", "UDZO", image], check=True)
    return image


def build_installer(out_dir: str) -> str:
    system = platform.system()
    if system == "Windows":
        return windows_installer(out_dir)
    if system == "Linux":
        return linux_installer(out_dir)
    if system == "Darwin":
        return macos_installer(out_dir)
    raise SystemExit(f"No installer format for {system}")


def report(path: str) -> None:
    print(f"Built {path} ({os.path.getsize(path) / (1024 * 1024):.1f} MB)")
    print(f"Checksum: {checksum(path)}", flush=True)


def main():
    os.chdir(ROOT)
    if "--checksum" in sys.argv:
        # CI re-runs this on files that came back signed from a signing service: the bytes changed, so the
        # .sha256 written at packaging time no longer matches what people download.
        for path in sys.argv[sys.argv.index("--checksum") + 1:]:
            print(f"Checksum: {checksum(path)}")
        return
    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    cmd = pyinstaller_command()
    print("Running:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)
    out_dir = find_output_dir()
    if platform.system() == "Windows":
        sign(os.path.join(out_dir, f"{APP_NAME}.exe"))
    add_extras(out_dir)
    report(package_archive(out_dir))
    if "--no-installer" in sys.argv:
        print("Installer skipped (--no-installer)")
    else:
        report(build_installer(out_dir))


if __name__ == "__main__":
    main()
