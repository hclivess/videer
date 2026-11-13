"""
AviSynth+ frameserver: QTGMC, the source filter and the user's own script lines, in an .avs that FFmpeg opens
directly.

On Windows every plugin is bundled in plugins/ and loaded by path. Those are Windows DLLs; anywhere else
(AviSynth+ has run on Linux and macOS since 3.5) the plugins are the ones installed on the system, which
AviSynth+ autoloads, and only the two portable .avsi scripts come from plugins/. What is installed differs
from machine to machine, so there the install is asked — through FFmpeg, the same way the encode will reach
it — rather than assumed (issue #21).

nnedi3, the interpolator QTGMC uses at most presets, has no Linux or macOS build as an AviSynth+ plugin. Without
it QTGMC still runs: its interpolation step is given to NNEDI3CL (the same network on OpenCL, which does build
there), failing that to yadifmod2, failing that to a plain bob. Checked against AviSynth+ 3.7.6 on Arch Linux.
"""

import os
import re
import subprocess
import sys
import tempfile
import multiprocessing
from dataclasses import dataclass
from typing import Dict, Any, FrozenSet, List, Optional, Tuple
from models.file_models import VideoFile
from utils import childproc
from utils.ffmpeg_utils import find_ffmpeg, has_audio_stream
from modules.frameservers.base import Frameserver, FrameserverError, FrameserverInput

WINDOWS = sys.platform.startswith('win')

# Source filters, best known first: (video function, audio function, name to show, arguments after the path).
# AVISource is Video for Windows and exists nowhere else, so off Windows one of these reads every source.
SOURCE_FILTERS: List[Tuple[str, str, str, str]] = [
    ("FFVideoSource", "FFAudioSource", "FFMS2", ", track=-1"),
    ("LWLibavVideoSource", "LWLibavAudioSource", "L-SMASH-Works (LSMASHSource)", ""),
    ("BSVideoSource", "BSAudioSource", "BestSource", ""),
]

# One function that only the plugin provides -> the plugin, for everything QTGMC calls.
QTGMC_PLUGIN_FUNCTIONS = {
    "MSuper": "mvtools2",
    "mt_lutxy": "masktools2",
    "RemoveGrain": "RgTools",
    "nnedi3": "nnedi3",
    "yadifmod2": "yadifmod2",
    "FFT3DFilter": "fft3dfilter",
}

# Asked about as well, though QTGMC does not call it: what can stand in for nnedi3 where that does not exist
NNEDI3CL = "NNEDI3CL"

# nnedi3() as QTGMC calls it, served by NNEDI3CL. The parameters are nnedi3's; `threads`, `opt` and `fapprox`
# mean nothing to an OpenCL filter and are accepted and dropped. U/V=false is nnedi3 for "luma only".
NNEDI3CL_SHIM = """\
function nnedi3(clip c, int "field", bool "dh", bool "Y", bool "U", bool "V", int "nsize", int "nns", int "qual", \\
                int "etype", int "pscrn", int "threads", int "opt", int "fapprox")
{
    chroma = default(U, true) || default(V, true)
    planes = (c.IsY() || !chroma) ? [0] : [0, 1, 2]
    return c.NNEDI3CL(field=default(field, -1), dh=default(dh, false), planes=planes, nsize=default(nsize, 6), \\
                      nns=default(nns, 1), qual=default(qual, 1), etype=default(etype, 0), pscrn=default(pscrn, 2))
}
"""

# Where AviSynth+ plugins end up that the library does not look. It autoloads from the prefix it was built
# with — /usr/lib/avisynth for a distribution's package — plus ~/.avisynth and ~/.local/lib/avisynth, while a
# plugin's own `cmake --install` defaults to /usr/local, and some distributions use lib64 or a multiarch
# folder. A plugin installed by the book was then "not installed". Naming a folder twice, or one that is
# not there, does no harm (checked against 3.7.6), but only those that exist are written.
EXTRA_PLUGIN_DIRS = (
    "/usr/local/lib/avisynth",
    "/usr/local/lib64/avisynth",
    "/usr/lib64/avisynth",
    f"/usr/lib/{os.uname().machine if hasattr(os, 'uname') else 'x86_64'}-linux-gnu/avisynth",
    "/usr/lib/avisynth",
    "/opt/homebrew/lib/avisynth",       # Homebrew on Apple silicon
    "/opt/local/lib/avisynth",          # MacPorts
)


def autoload_lines(folders: Optional[Tuple[str, ...]] = None) -> List[str]:
    """
    AddAutoloadDir() for every plugin folder that exists. They have to be the first lines of a script:
    AviSynth+ refuses to change the list once anything has made it load the plugins.
    """
    if WINDOWS:
        return []
    folders = EXTRA_PLUGIN_DIRS if folders is None else folders
    return [f'AddAutoloadDir({avs_string(folder)})' for folder in folders if os.path.isdir(folder)]


_PROBE_MARK = re.compile(r'videer-probe:\[(.*?)\]')


@dataclass(frozen=True)
class AviSynthInstall:
    """What the AviSynth+ on this machine can do, as FFmpeg sees it"""

    error: Optional[str] = None
    """Why FFmpeg cannot run an AviSynth+ script at all, or None when it can."""

    functions: FrozenSet[str] = frozenset()
    """Which of the functions asked about exist (plugins autoloaded)."""

    def source_filter(self) -> Optional[Tuple[str, str, str, str]]:
        for entry in SOURCE_FILTERS:
            if entry[0] in self.functions:
                return entry
        return None


def avs_string(text: str) -> str:
    """
    An AviSynth string literal. There are no escapes; a text holding a double quote — legal in a file name
    everywhere but Windows — goes between triple quotes instead.
    """
    return f'"""{text}"""' if '"' in text else f'"{text}"'


_install: Optional[AviSynthInstall] = None


def _ffmpeg_reads_avisynth(ffmpeg: str) -> bool:
    """Whether this FFmpeg has the avisynth demuxer. A build that cannot be asked counts as yes."""
    try:
        result = childproc.run([ffmpeg, '-hide_banner', '-demuxers'], text=True, timeout=30)
    except (subprocess.SubprocessError, OSError):
        return True
    names = {parts[1] for parts in (line.split() for line in (result.stdout or '').splitlines())
             if len(parts) >= 2 and parts[1] != '='}
    return 'avisynth' in names if names else True


def probe_avisynth(ffmpeg: Optional[str] = None) -> AviSynthInstall:
    """
    Ask the installed AviSynth+ what it has, by having FFmpeg open a script that names what it finds and stops.

    Looking for libavisynth on disk would answer a different question: the encode fails unless *this* FFmpeg
    was built with AviSynth support, can load the library, and the library autoloads the plugins. A script
    that runs proves all three at once, and its failure carries FFmpeg's own explanation.
    """
    ffmpeg = ffmpeg or find_ffmpeg()
    if not ffmpeg:
        return AviSynthInstall(error="FFmpeg was not found, so AviSynth+ could not be checked.")
    if not _ffmpeg_reads_avisynth(ffmpeg):
        return AviSynthInstall(error="This FFmpeg was built without AviSynth support (it has no 'avisynth' "
                                     "demuxer), so it cannot open an AviSynth+ script. Use a build configured "
                                     "with --enable-avisynth — Tools ▸ FFmpeg Features can fetch one where "
                                     "builds are published — or disable AviSynth+ and use bwdif/yadif.")

    asked = [name for entry in SOURCE_FILTERS for name in entry[:2]] + list(QTGMC_PLUGIN_FUNCTIONS)
    lines = autoload_lines() + ['found = ""']
    lines += [f'found = FunctionExists("{name}") ? found + "{name} " : found' for name in asked]
    # NNEDI3CL being loaded is not NNEDI3CL working: it needs an OpenCL device and its weights file, and says
    # so only when a clip is built with it. So build one.
    lines += ['usable = false',
              'try {',
              f'    BlankClip(length=1, width=64, height=64, pixel_type="YV12").{NNEDI3CL}(field=1)',
              '    usable = true',
              '} catch (problem) {',
              '}',
              f'found = usable ? found + "{NNEDI3CL} " : found']
    lines.append('Assert(false, "videer-probe:[" + found + "]")')

    output = ''
    try:
        with tempfile.TemporaryDirectory(prefix='videer-avs-') as folder:
            script = os.path.join(folder, 'probe.avs')
            with open(script, 'w') as avs:
                avs.write('\n'.join(lines) + '\n')
            result = childproc.run([ffmpeg, '-hide_banner', '-nostdin', '-f', 'avisynth', '-i', script],
                                   stdin=subprocess.DEVNULL, text=True, errors='replace', timeout=60)
            output = (result.stderr or '') + (result.stdout or '')
    except subprocess.TimeoutExpired:
        return AviSynthInstall(error="AviSynth+ did not answer within a minute when FFmpeg opened a test "
                                     "script; a plugin it autoloads may be hanging.")
    except (subprocess.SubprocessError, OSError) as e:
        return AviSynthInstall(error=f"AviSynth+ could not be checked: {e}")

    found = _PROBE_MARK.search(output)
    if found:
        return AviSynthInstall(functions=frozenset(found.group(1).split()))
    said = [line.strip() for line in output.splitlines() if line.strip()]
    reason = said[-1] if said else "FFmpeg gave no reason"
    library = "libavisynth.dylib" if sys.platform == 'darwin' else "libavisynth.so"
    return AviSynthInstall(error=f"FFmpeg could not run an AviSynth+ script — is AviSynth+ ({library}) "
                                 f"installed where FFmpeg can load it? FFmpeg said: {reason}")


class AviSynthFrameserver(Frameserver):
    """Writes the AviSynth+ script; FFmpeg reads it as its input, audio included"""

    name = "AviSynth+"
    script_extension = ".avs"

    # Every plugin QTGMC can call across all its presets is bundled in plugins/:
    # "Ultra Fast" uses yadifmod2, "Very Slow" uses FFT3DFilter (needs FFTW).
    PLUGIN_DLLS = [
        "masktools2.dll",
        "mvtools2.dll",
        "nnedi3.dll",
        "ffms2.dll",
        "RgTools.dll",
        "yadifmod2.dll",
        "fft3dfilter.dll",
    ]
    PLUGIN_SCRIPTS = ["QTGMC.avsi", "Zs_RF_Shared.avsi"]
    # Runtime DLLs loaded by plugins via LoadLibrary (found through PATH, not LoadPlugin)
    RUNTIME_DLLS = ["libfftw3f-3.dll"]

    def __init__(self, settings: Dict[str, Any]):
        super().__init__(settings)
        self.cpu_count = multiprocessing.cpu_count()
        self.plugins_path = self._get_plugins_path()

    @staticmethod
    def _system_install(refresh: bool = False) -> AviSynthInstall:
        """
        The system's AviSynth+ (non-Windows). Asked afresh at every settings check — a plugin may have been
        installed since — and remembered between the files of a run, which each get a frameserver of their own.
        """
        global _install
        if _install is None or refresh:
            _install = probe_avisynth()
        return _install
    
    def _get_plugins_path(self) -> str:
        """Get path to AviSynth plugins directory"""
        # modules/frameservers/ -> the application root, where plugins/ lives (also inside a frozen bundle)
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(root, "plugins")
    
    def prepare(self, video_file: VideoFile, source: str) -> FrameserverInput:
        script = self.script_path(video_file)
        video_file.script_file = script
        self._index_files: List[str] = []
        try:
            with open(script, "w") as avs:
                self._write_plugins(avs)
                self._write_mt_setup(avs)
                self._write_source(avs, source)
                self._write_processing(avs)
                self._write_custom_extras(avs)
                self._write_deinterlacing(avs)
                self._write_prefetch(avs)
        except OSError as e:
            raise FrameserverError(f"Failed to create AviSynth script: {e}") from e
        # An index that was already there is the user's own, not this encode's to delete
        video_file.index_files = [index for index in self._index_files if not os.path.exists(index)]
        # The script serves audio as well (AudioDub / AVISource), and the user's own lines may trim or
        # retime it — so it is the only input; a companion's audio could no longer be trusted to line up.
        return FrameserverInput(script=script, video_args=['-i', script])

    def problems(self) -> List[str]:
        if WINDOWS:
            missing = self.get_missing_plugins()
            return ["Missing AviSynth plugins in plugins/: " + ", ".join(missing)] if missing else []
        return self._system_problems()

    def _system_problems(self) -> List[str]:
        """Off Windows nothing is bundled but the scripts: say what this machine's AviSynth+ is missing"""
        install = self._system_install(refresh=True)
        if install.error:
            return [install.error]
        issues = []
        if install.source_filter() is None:
            issues.append("AviSynth+ has no source filter installed, so it cannot open a video: install "
                          + ", ".join(entry[2] for entry in SOURCE_FILTERS[:-1])
                          + f" or {SOURCE_FILTERS[-1][2]} as an AviSynth+ plugin.")
        if self._uses_qtgmc():
            scripts = [name for name in self.PLUGIN_SCRIPTS
                       if not os.path.exists(os.path.join(self.plugins_path, name))]
            if scripts:
                issues.append("Missing AviSynth scripts in plugins/: " + ", ".join(scripts))
            # The interpolators are not on this list: QTGMC runs without them, see _interpolation()
            plugins = [plugin for function, plugin in QTGMC_PLUGIN_FUNCTIONS.items()
                       if plugin in self._qtgmc_plugins() and plugin not in ("nnedi3", "yadifmod2")
                       and function not in install.functions]
            if plugins:
                issues.append(f"QTGMC ({self.settings.get('preset', 'Medium')}) needs AviSynth+ plugins that are "
                              f"not installed on this system: {', '.join(plugins)}. Install them — AviSynth+ "
                              f"loads plugins from ~/.local/lib/avisynth, /usr/lib/avisynth and "
                              f"/usr/local/lib/avisynth — or use QTempGaussMC or bwdif instead.")
        return issues

    def _interpolation(self) -> Tuple[Optional[str], bool, Optional[str]]:
        """
        How QTGMC interpolates here: (EdiMode to pass or None for the preset's own, whether nnedi3() has to be
        defined on top of NNEDI3CL, what to tell the user or None).

        On Windows the preset's own interpolator is bundled. Elsewhere it is whatever is installed, and nnedi3
        cannot be: it has no build outside Windows. The rest of QTGMC — the motion-compensated temporal
        smoothing that makes it QTGMC — is the same either way; what changes is the first guess at the missing
        lines that it refines.
        """
        if WINDOWS or not self._uses_qtgmc():
            return None, False, None
        wanted = self._qtgmc_plugins()
        have = self._system_install().functions
        if "nnedi3" in wanted:
            if "nnedi3" in have:
                return None, False, None
            if NNEDI3CL in have:
                return None, True, ("nnedi3 is not installed (it has no build for this system); QTGMC "
                                    "interpolates with NNEDI3CL, the same network on OpenCL")
            missing = "nnedi3 is not installed (it has no build for this system) and NNEDI3CL is not usable"
        elif "yadifmod2" in wanted:
            if "yadifmod2" in have:
                return None, False, None
            missing = "yadifmod2 is not installed"
        else:
            return None, False, None
        if "yadifmod2" in have:
            return "RepYadif", False, (f"{missing}; QTGMC interpolates with yadifmod2 instead, as its Ultra Fast "
                                       f"preset does. Fine detail and diagonals are a little softer; "
                                       f"QTempGaussMC gives the full result")
        return "Bob", False, (f"{missing}, nor is yadifmod2; QTGMC interpolates with a plain bob instead, as its "
                              f"Draft preset does. Fine detail and diagonals are softer; QTempGaussMC gives the "
                              f"full result")

    def notes(self) -> List[str]:
        note = self._interpolation()[2]
        return [note] if note else []

    def _qtgmc_plugins(self) -> List[str]:
        """The plugins QTGMC calls at the selected preset"""
        preset = self.settings.get('preset', 'Medium')
        plugins = ["mvtools2", "masktools2", "RgTools"]
        if preset == "Ultra Fast":
            plugins.append("yadifmod2")       # EdiMode "RepYadif"
        elif preset != "Draft":
            plugins.append("nnedi3")
        if preset in ("Placebo", "Very Slow"):
            plugins.append("fft3dfilter")     # NoiseProcess 2 denoises with fft3df
        return plugins

    def child_env(self, env: Dict[str, str]) -> Dict[str, str]:
        # Plugins that pull in runtime DLLs via LoadLibrary (fft3dfilter -> libfftw3f-3.dll) resolve them
        # through PATH, so expose plugins/.
        env = dict(env)
        env['PATH'] = self.plugins_path + os.pathsep + env.get('PATH', '')
        return env
    
    def _write_plugins(self, avs_file):
        """Write plugin loading section"""
        if WINDOWS:
            for plugin in self.PLUGIN_DLLS:
                plugin_path = os.path.join(self.plugins_path, plugin)
                avs_file.write(f'LoadPlugin("{plugin_path}")\n')
        else:
            # The bundled DLLs are Windows binaries. Here the plugins are the system's own, which AviSynth+
            # autoloads; only the scripts below are portable, and only QTGMC needs them.
            avs_file.write('# plugins: autoloaded from the system AviSynth+ plugin directories\n')
            for line in autoload_lines():
                avs_file.write(line + '\n')
            if not self._uses_qtgmc():
                avs_file.write('\n')
                return

        if not WINDOWS and self._interpolation()[1]:
            avs_file.write('# nnedi3 does not exist on this system: QTGMC\'s calls to it go to NNEDI3CL\n')
            avs_file.write(NNEDI3CL_SHIM)

        # Import scripts
        for script in self.PLUGIN_SCRIPTS:
            script_path = os.path.join(self.plugins_path, script)
            avs_file.write(f'Import({avs_string(script_path)})\n')
        
        avs_file.write('\n')
    
    def _write_mt_setup(self, avs_file):
        """Write multi-threading setup"""
        avs_file.write('# Multi-threading setup\n')
        avs_file.write('SetFilterMTMode("DEFAULT_MT_MODE", 2)\n')
        
        mt_modes = {
            "QTGMC": 3,
            "nnedi3": 3,
            "MVAnalyse": 3,
            "MVDegrain1": 3,
            "MVDegrain2": 3,
            "MVDegrain3": 3,
            "FFVideoSource": 3,
            "LWLibavVideoSource": 3,
            "BSVideoSource": 3,
        }
        
        for filter_name, mode in mt_modes.items():
            avs_file.write(f'SetFilterMTMode("{filter_name}", {mode})\n')
        
        avs_file.write('\n')
    
    def _write_source(self, avs_file, source: str):
        """Write source loading section"""
        avs_file.write('# Source loading\n')

        video_source, audio_source, _name, arguments = SOURCE_FILTERS[0]
        if not WINDOWS:
            # Whichever source filter this system has. With none, FFMS2 is written anyway: AviSynth+ then
            # fails on a line that names what is missing, which the settings check has already said.
            video_source, audio_source, _name, arguments = \
                self._system_install().source_filter() or SOURCE_FILTERS[0]

        if self.settings.get('use_ffms2', False) or not WINDOWS:
            # An indexing source filter, which reads anything FFmpeg can. Off Windows it is the only kind
            # there is — AVISource is Video for Windows — so it is used whatever the FFMS2 checkbox says.
            if video_source == "LWLibavVideoSource":
                self._index_files.append(f"{source}.lwi")
            avs_file.write(f'v = {video_source}({avs_string(source)}{arguments})\n')
            if has_audio_stream(source):
                avs_file.write(f'a = {audio_source}({avs_string(source)}{arguments})\n')
                avs_file.write('AudioDub(v, a)\n')
            else:
                # The audio source throws when there is no audio track, and that error fails the whole file.
                # Silent sources are ordinary here — a camera in video-only mode, a capture with the audio
                # card unplugged — so serve the video on its own instead.
                avs_file.write('# no audio track in the source; serving video only\n')
                avs_file.write('v\n')
        else:
            # Use AVISource (requires AVI input)
            avs_file.write(f'AVISource({avs_string(source)}, audio=true)\n')
        
        avs_file.write('\n')
    
    def _write_processing(self, avs_file):
        """Normalise to 4:2:0 (no-op for YV12 sources, converts RGB/YUY2 inputs).
        QTGMC on 4:4:4 costs ~2x for chroma that ffmpeg drops again with yuv420p."""
        avs_file.write('# Color conversion\n')
        avs_file.write('ConvertToYV12(matrix="rec709")\n')
        avs_file.write('\n')
    
    def _write_custom_extras(self, avs_file):
        """Write custom AviSynth extras from user settings"""
        extras = self.settings.get('avisynth_extras', '').strip()
        if extras:
            avs_file.write('# Custom processing\n')
            avs_file.write(extras + '\n')
            avs_file.write('\n')
    
    def _requested_threads(self) -> int:
        """The user's CPU budget, defaulting to every core"""
        try:
            threads = int(self.settings.get('threads') or self.cpu_count)
        except (TypeError, ValueError):
            threads = self.cpu_count
        return max(1, threads)

    def _uses_qtgmc(self) -> bool:
        return bool(self.settings.get('deinterlace', False)) and \
            self.settings.get('deinterlacer', 'qtgmc') == 'qtgmc'

    def _edi_threads(self) -> int:
        """
        Threads for QTGMC's internal (nnedi3) threading. Prefetch() already runs
        several frames in parallel, so give each frame a small slice of the cores
        instead of threads*threads oversubscription.
        """
        return max(1, min(4, self._requested_threads() // 2))

    def _prefetch_threads(self) -> int:
        """
        How many frames to run in parallel. Concurrency here multiplies: every prefetched frame can run up to
        EdiThreads nnedi3 threads of its own, so Prefetch(N) with EdiThreads(E) asks for N x E workers on top
        of FFmpeg's own encoder threads. Divide the budget instead of spending it twice.
        """
        threads = self._requested_threads()
        if self._uses_qtgmc():
            return max(1, threads // self._edi_threads())
        return threads

    def _write_deinterlacing(self, avs_file):
        """Write QTGMC deinterlacing section if enabled (ffmpeg handles yadif/bwdif)"""
        if not self._uses_qtgmc():
            return

        avs_file.write('# Deinterlacing\n')
        
        # Field order
        if self.settings.get('tff', False):
            avs_file.write('AssumeTFF()\n')
        else:
            avs_file.write('AssumeBFF()\n')
        
        # QTGMC deinterlacing — all presets are usable, plugins are bundled
        preset = self.settings.get('preset', 'Medium')
        arguments = [f'Preset="{preset}"']
        edi_mode = self._interpolation()[0]
        if edi_mode:
            arguments.append(f'EdiMode="{edi_mode}"')       # the preset's interpolator is not installed here
        if self.settings.get('reduce_fps', False):
            arguments.append('FPSDivisor=2')                # halve the frame rate
        arguments.append(f'EdiThreads={self._edi_threads()}')
        avs_file.write(f'QTGMC({", ".join(arguments)})\n')
        
        avs_file.write('\n')
    
    def _write_prefetch(self, avs_file):
        """Write prefetch for multi-threading"""
        threads = self._prefetch_threads()
        avs_file.write('# Enable multi-threaded processing\n')
        avs_file.write(f'Prefetch({threads})\n')
    
    def validate_plugins(self) -> Dict[str, bool]:
        """
        Check which AviSynth plugins are available
        Returns dict of plugin_name: available
        """
        required_plugins = {name: False for name in
                            self.PLUGIN_DLLS + self.PLUGIN_SCRIPTS + self.RUNTIME_DLLS}
        
        for plugin in required_plugins:
            plugin_path = os.path.join(self.plugins_path, plugin)
            required_plugins[plugin] = os.path.exists(plugin_path)
        
        return required_plugins
    
    def get_missing_plugins(self) -> list:
        """Get list of missing required plugins"""
        plugin_status = self.validate_plugins()
        return [name for name, available in plugin_status.items() if not available]
