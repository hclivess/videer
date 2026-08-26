"""
AviSynth Handler for videer
Handles AviSynth script generation and management
"""

import os
import multiprocessing
from typing import Dict, Any, Optional
from models.file_models import VideoFile
from utils.ffmpeg_utils import has_audio_stream


class AviSynthHandler:
    """Handles AviSynth+ script generation"""

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
        self.settings = settings
        self.cpu_count = multiprocessing.cpu_count()
        self.plugins_path = self._get_plugins_path()
    
    def _get_plugins_path(self) -> str:
        """Get path to AviSynth plugins directory"""
        current_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(os.path.dirname(current_dir), "plugins")
    
    def create_script(self, video_file: VideoFile) -> bool:
        """
        Create AviSynth script for the video file
        Returns True if successful
        """
        if not video_file.avs_file:
            # Next to the source, never in the process CWD
            video_file.avs_file = os.path.join(video_file.directory, f"{video_file.basename}.avs")
        
        try:
            with open(video_file.avs_file, "w") as avs:
                self._write_plugins(avs)
                self._write_mt_setup(avs)
                self._write_source(avs, video_file)
                self._write_processing(avs)
                self._write_custom_extras(avs)
                self._write_deinterlacing(avs)
                self._write_prefetch(avs)
            
            return True
        except Exception as e:
            if video_file.logger:
                video_file.logger.error(f"Failed to create AVS script: {e}")
            return False
    
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
    
    def _write_source(self, avs_file, video_file: VideoFile):
        """Write source loading section"""
        avs_file.write('# Source loading\n')
        
        if self.settings.get('use_ffms2', False):
            # Use FFMS2 for better compatibility
            avs_file.write(f'v = FFVideoSource("{video_file.filepath}", track=-1)\n')
            if has_audio_stream(video_file.filepath):
                avs_file.write(f'a = FFAudioSource("{video_file.filepath}", track=-1)\n')
                avs_file.write('AudioDub(v, a)\n')
            else:
                # FFAudioSource throws when there is no audio track, and that error fails the whole file.
                # Silent sources are ordinary here — a camera in video-only mode, a capture with the audio
                # card unplugged — so serve the video on its own instead.
                avs_file.write('# no audio track in the source; serving video only\n')
                avs_file.write('v\n')
        else:
            # Use AVISource (requires AVI input)
            avs_file.write(f'AVISource("{video_file.filepath}", audio=true)\n')
        
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
        if not self.settings.get('deinterlace', False):
            return
        if self.settings.get('deinterlacer', 'qtgmc') != 'qtgmc':
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
        avs_file.write(f'# Enable multi-threaded processing\n')
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