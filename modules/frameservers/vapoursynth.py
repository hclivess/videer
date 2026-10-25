"""
VapourSynth frameserver: QTempGaussMC, the vs-jetpack rewrite of QTGMC, served by `vspipe` into FFmpeg.

Why vspipe rather than FFmpeg's own `-f vapoursynth` demuxer: the demuxer finds VSScript on Windows only
through a registry entry an installer writes, and distribution FFmpeg builds are mostly compiled without it.
vspipe is part of every VapourSynth install, pip's included, and a y4m pipe is understood by every FFmpeg.

A pipe carries video and nothing else, so FFmpeg takes everything else — audio, subtitles, chapters, tags —
from the source file as a second input. The script never trims or retimes, which is what makes that safe.
"""

import os
import shutil
import sys
import tempfile
import threading
import multiprocessing
from typing import Dict, Any, List, Optional

from config import DATA_DIR
from models.file_models import VideoFile
from utils import childproc
from utils.ffmpeg_utils import probe_video_start_offset
from modules.frameservers.base import Frameserver, FrameserverError, FrameserverInput


INSTALL_HINT = "install it from the Processing tab (Install VapourSynth…) or Tools ▸ VapourSynth"

# Where videer's own VapourSynth install lives (modules/vapoursynth_installer.py builds it). The marker is
# written last, so a folder without it is an interrupted install and is never used.
MANAGED_VS_DIR = os.path.join(DATA_DIR, "vapoursynth")
MANAGED_MARKER = os.path.join(MANAGED_VS_DIR, "installed.json")
MANAGED_PYTHON_HOME = os.path.join(MANAGED_VS_DIR, "python")

# A clip QTempGaussMC is run over to prove the whole chain resolves: every plugin namespace the default
# settings call is touched while the graph is built, and rendering a frame proves they load.
_CHECK_SCRIPT = '''\
import vapoursynth as vs
from vapoursynth import core
from vsdeinterlace import QTempGaussMC
core.bs.VideoSource
clip = core.std.BlankClip(width=320, height=240, format=vs.YUV420P8, length=10, fpsnum=25, fpsden=1)
clip = core.std.SetFieldBased(clip, 2)
clip = QTempGaussMC().deinterlace(clip, tff=True)
clip.get_frame(0)
clip.set_output()
'''

# Environment check results per vspipe path. A check costs a couple of seconds; the cache is dropped when
# videer installs or removes VapourSynth itself, and by the Re-check button for anything done outside it.
_checked: Dict[str, Optional[str]] = {}
_check_lock = threading.Lock()


def vspipe_in(python_home: str) -> str:
    """Where pip puts vspipe in a Python installation"""
    return (os.path.join(python_home, "Scripts", "vspipe.exe") if sys.platform == 'win32'
            else os.path.join(python_home, "bin", "vspipe"))


def managed_vspipe() -> Optional[str]:
    """The vspipe of videer's own, completed install, or None"""
    if not os.path.isfile(MANAGED_MARKER):
        return None
    vspipe = vspipe_in(MANAGED_PYTHON_HOME)
    return vspipe if os.path.isfile(vspipe) else None


def find_vspipe() -> Optional[str]:
    """
    The vspipe to serve with: VIDEER_VSPIPE if set; then videer's own install, which was put there for this
    purpose; then, from source, the one beside the Python running videer (`pip install -r
    requirements-vapoursynth.txt`); then the PATH.
    """
    override = os.environ.get('VIDEER_VSPIPE')
    if override:
        return override if os.path.isfile(override) else None
    managed = managed_vspipe()
    if managed:
        return managed
    name = 'vspipe.exe' if sys.platform == 'win32' else 'vspipe'
    if not getattr(sys, 'frozen', False):
        # A venv keeps it beside python (Scripts/ or bin/); a system Python on Windows in Scripts/ below it
        home = os.path.dirname(sys.executable)
        for folder in (home, os.path.join(home, 'Scripts'), os.path.join(home, 'bin')):
            candidate = os.path.join(folder, name)
            if os.path.isfile(candidate):
                return candidate
    return shutil.which('vspipe')


def check_environment(vspipe: str) -> Optional[str]:
    """None when vspipe can run QTempGaussMC; otherwise the reason it cannot, in vspipe's own words"""
    with _check_lock:
        if vspipe in _checked:
            return _checked[vspipe]
        _checked[vspipe] = reason = _run_check(vspipe)
        return reason


def forget_checks() -> None:
    with _check_lock:
        _checked.clear()


def _run_check(vspipe: str) -> Optional[str]:
    fd, path = tempfile.mkstemp(prefix='videer-vscheck-', suffix='.vpy')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(_CHECK_SCRIPT)
        try:
            result = childproc.run([vspipe, '--info', path], text=True, encoding='utf-8', errors='replace',
                                   timeout=120)
        except Exception as exc:                         # noqa: BLE001 - any failure to launch is the answer
            return f"vspipe could not be run: {exc}"
        if result.returncode == 0:
            return None
        lines = [line.strip() for line in (result.stderr or result.stdout or '').splitlines() if line.strip()]
        return lines[-1] if lines else f"vspipe exited with code {result.returncode}"
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


class VapourSynthFrameserver(Frameserver):
    """Writes a .vpy running QTempGaussMC; vspipe serves it to FFmpeg as y4m on stdin"""

    name = "VapourSynth"
    script_extension = ".vpy"

    def __init__(self, settings: Dict[str, Any]):
        super().__init__(settings)
        self.vspipe = find_vspipe()

    def prepare(self, video_file: VideoFile, source: str) -> FrameserverInput:
        if not self.vspipe:
            raise FrameserverError(f"vspipe not found — QTempGaussMC needs VapourSynth: {INSTALL_HINT}")
        script = self.script_path(video_file)
        video_file.script_file = script
        try:
            with open(script, 'w', encoding='utf-8') as handle:
                handle.write(self.render_script(source))
        except OSError as e:
            raise FrameserverError(f"Failed to create VapourSynth script: {e}") from e
        return FrameserverInput(
            script=script,
            video_args=['-f', 'yuv4mpegpipe', '-i', 'pipe:0'],
            producer=[self.vspipe, '-c', 'y4m', script, '-'],
            companion=source,
            video_offset=probe_video_start_offset(source),
        )

    def render_script(self, source: str) -> str:
        """The .vpy for one source. Paths go in through repr(), so quotes and backslashes cannot break it."""
        tff = bool(self.settings.get('tff', False))
        # QTempGaussMC's defaults are its tuned configuration — the rewrite retired the preset system, so the
        # speed preset belongs to the encoder alone here. Halving the rate is the motion-blur stage's divisor.
        options = 'motion_blur_fps_divisor=2' if self.settings.get('reduce_fps', False) else ''
        return (
            "# Written by videer for one encode; deleted when it finishes\n"
            "import vapoursynth as vs\n"
            "from vapoursynth import core\n"
            "from vsdeinterlace import QTempGaussMC\n"
            "\n"
            f"core.num_threads = {self._threads()}\n"
            f"clip = core.bs.VideoSource(source={source!r})\n"
            f"clip = QTempGaussMC({options}).deinterlace(clip, tff={tff})\n"
            "clip.set_output()\n"
        )

    def _threads(self) -> int:
        try:
            threads = int(self.settings.get('threads') or 0)
        except (TypeError, ValueError):
            threads = 0
        return max(1, threads or multiprocessing.cpu_count())

    def problems(self) -> List[str]:
        issues = []
        if self.settings.get('use_avisynth'):
            issues.append("QTempGaussMC runs in VapourSynth, and AviSynth+ processing cannot run on the same "
                          "encode — turn AviSynth+ off, or pick QTGMC (AviSynth+) instead.")
        if not self.vspipe:
            issues.append(f"QTempGaussMC needs VapourSynth, and no vspipe was found. Install it with: "
                          f"{INSTALL_HINT}")
            return issues
        reason = check_environment(self.vspipe)
        if reason:
            issues.append(f"VapourSynth cannot run QTempGaussMC ({self.vspipe}): {reason}. "
                          f"If something is missing: {INSTALL_HINT}")
        return issues

