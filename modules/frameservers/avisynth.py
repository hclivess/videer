"""
AviSynth+ frameserver: QTGMC, the source filter and the user's own script lines, in an .avs that FFmpeg opens
directly.
"""

import os
import sys
import multiprocessing
from typing import Dict, Any, List
from models.file_models import VideoFile
from utils.ffmpeg_utils import has_audio_stream
from modules.frameservers.base import Frameserver, FrameserverError, FrameserverInput


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
    
    def _get_plugins_path(self) -> str:
        """Get path to AviSynth plugins directory"""
        # modules/frameservers/ -> the application root, where plugins/ lives (also inside a frozen bundle)
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(root, "plugins")
    
    def prepare(self, video_file: VideoFile, source: str) -> FrameserverInput:
        script = self.script_path(video_file)
        video_file.script_file = script
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
        # The script serves audio as well (AudioDub / AVISource), and the user's own lines may trim or
        # retime it — so it is the only input; a companion's audio could no longer be trusted to line up.
        return FrameserverInput(script=script, video_args=['-i', script])

    def problems(self) -> List[str]:
        issues = []
        if not sys.platform.startswith('win'):
            issues.append("AviSynth+ is only available on Windows; disable it or use bwdif/yadif.")
        missing = self.get_missing_plugins()
        if missing:
            issues.append("Missing AviSynth plugins in plugins/: " + ", ".join(missing))
        return issues

    def child_env(self, env: Dict[str, str]) -> Dict[str, str]:
        # Plugins that pull in runtime DLLs via LoadLibrary (fft3dfilter -> libfftw3f-3.dll) resolve them
        # through PATH, so expose plugins/.
        env = dict(env)
        env['PATH'] = self.plugins_path + os.pathsep + env.get('PATH', '')
        return env
    
    def _write_plugins(self, avs_file):
        """Write plugin loading section"""
        for plugin in self.PLUGIN_DLLS:
            plugin_path = os.path.join(self.plugins_path, plugin)
            avs_file.write(f'LoadPlugin("{plugin_path}")\n')
        
        # Import scripts
        for script in self.PLUGIN_SCRIPTS:
            script_path = os.path.join(self.plugins_path, script)
            avs_file.write(f'Import("{script_path}")\n')
        
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
            "FFVideoSource": 3
        }
        
        for filter_name, mode in mt_modes.items():
            avs_file.write(f'SetFilterMTMode("{filter_name}", {mode})\n')
        
        avs_file.write('\n')
    
    def _write_source(self, avs_file, source: str):
        """Write source loading section"""
        avs_file.write('# Source loading\n')
        
        if self.settings.get('use_ffms2', False):
            # Use FFMS2 for better compatibility
            avs_file.write(f'v = FFVideoSource("{source}", track=-1)\n')
            if has_audio_stream(source):
                avs_file.write(f'a = FFAudioSource("{source}", track=-1)\n')
                avs_file.write('AudioDub(v, a)\n')
            else:
                # FFAudioSource throws when there is no audio track, and that error fails the whole file.
                # Silent sources are ordinary here — a camera in video-only mode, a capture with the audio
                # card unplugged — so serve the video on its own instead.
                avs_file.write('# no audio track in the source; serving video only\n')
                avs_file.write('v\n')
        else:
            # Use AVISource (requires AVI input)
            avs_file.write(f'AVISource("{source}", audio=true)\n')
        
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
        threads = self._edi_threads()
        
        if self.settings.get('reduce_fps', False):
            # Reduce frame rate (halve FPS)
            avs_file.write(f'QTGMC(Preset="{preset}", FPSDivisor=2, EdiThreads={threads})\n')
        else:
            # Keep original frame rate
            avs_file.write(f'QTGMC(Preset="{preset}", EdiThreads={threads})\n')
        
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
