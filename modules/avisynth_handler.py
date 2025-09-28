"""
AviSynth Handler for videer
Handles AviSynth script generation and management
"""

import os
import multiprocessing
from typing import Dict, Any, Optional
from models.file_models import VideoFile


class AviSynthHandler:
    """Handles AviSynth+ script generation"""
    
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
            video_file.avs_file = f"{video_file.basename}.avs"
        
        try:
            with open(video_file.avs_file, "w") as avs:
                self._write_plugins(avs)
                self._write_mt_setup(avs)
                self._write_source(avs, video_file)
                self._write_processing(avs)
                self._write_custom_extras(avs)
                self._write_deinterlacing(avs)
                self._write_par_dar_corrections(avs)
                self._write_prefetch(avs)
            
            return True
        except Exception as e:
            if video_file.logger:
                video_file.logger.error(f"Failed to create AVS script: {e}")
            return False
    
    def _write_plugins(self, avs_file):
        """Write plugin loading section"""
        plugins = [
            "masktools2.dll",
            "mvtools2.dll",
            "nnedi3.dll",
            "ffms2.dll",
            "RgTools.dll"
        ]
        
        for plugin in plugins:
            plugin_path = os.path.join(self.plugins_path, plugin)
            avs_file.write(f'LoadPlugin("{plugin_path}")\n')
        
        # Import scripts
        scripts = [
            "QTGMC.avsi",
            "Zs_RF_Shared.avsi"
        ]
        
        for script in scripts:
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
            avs_file.write(f'a = FFAudioSource("{video_file.filepath}", track=-1)\n')
            avs_file.write('AudioDub(v, a)\n')
        else:
            # Use AVISource (requires AVI input)
            avs_file.write(f'AVISource("{video_file.filepath}", audio=true)\n')
        
        avs_file.write('\n')
    
    def _write_processing(self, avs_file):
        """Write basic processing"""
        avs_file.write('# Color conversion\n')
        avs_file.write('ConvertToYV24(matrix="rec709")\n')
        avs_file.write('\n')
    
    def _write_custom_extras(self, avs_file):
        """Write custom AviSynth extras from user settings"""
        extras = self.settings.get('avisynth_extras', '').strip()
        if extras:
            avs_file.write('# Custom processing\n')
            avs_file.write(extras + '\n')
            avs_file.write('\n')
    
    def _write_deinterlacing(self, avs_file):
        """Write deinterlacing section if enabled"""
        if not self.settings.get('deinterlace', False):
            return
        
        avs_file.write('# Deinterlacing\n')
        
        # Field order
        if self.settings.get('tff', False):
            avs_file.write('AssumeTFF()\n')
        else:
            avs_file.write('AssumeBFF()\n')
        
        # QTGMC deinterlacing
        preset = self.settings.get('preset', 'Medium')
        threads = self.settings.get('threads', self.cpu_count)
        
        if self.settings.get('reduce_fps', False):
            # Reduce frame rate (halve FPS)
            avs_file.write(f'QTGMC(Preset="{preset}", FPSDivisor=2, EdiThreads={threads})\n')
        else:
            # Keep original frame rate
            avs_file.write(f'QTGMC(Preset="{preset}", EdiThreads={threads})\n')
        
        avs_file.write('\n')
    
    def _write_par_dar_corrections(self, avs_file):
        """Write PAR/DAR correction if needed"""
        par_mode = self.settings.get('par_mode', 'auto')
        dar_mode = self.settings.get('dar_mode', 'auto')
        
        if par_mode != 'auto' or dar_mode != 'auto':
            avs_file.write('# Aspect ratio corrections\n')
            
            # PAR correction
            if par_mode == 'custom':
                par_value = self.settings.get('par_custom', '1:1')
                if ':' in par_value:
                    num, den = par_value.split(':')
                    avs_file.write(f'# Custom PAR: {par_value}\n')
                    # You could add resize operations here if needed
            elif par_mode != 'auto':
                par_value = self.settings.get('par_value', '1:1')
                avs_file.write(f'# PAR preset: {par_value}\n')
            
            # DAR correction
            if dar_mode == 'custom':
                dar_value = self.settings.get('dar_custom', '16:9')
                avs_file.write(f'# Custom DAR: {dar_value}\n')
            elif dar_mode != 'auto':
                dar_value = self.settings.get('dar_value', '16:9')
                avs_file.write(f'# DAR preset: {dar_value}\n')
            
            avs_file.write('\n')
    
    def _write_prefetch(self, avs_file):
        """Write prefetch for multi-threading"""
        threads = self.settings.get('threads', self.cpu_count)
        avs_file.write(f'# Enable multi-threaded processing\n')
        avs_file.write(f'Prefetch({threads})\n')
    
    def validate_plugins(self) -> Dict[str, bool]:
        """
        Check which AviSynth plugins are available
        Returns dict of plugin_name: available
        """
        required_plugins = {
            "masktools2.dll": False,
            "mvtools2.dll": False,
            "nnedi3.dll": False,
            "ffms2.dll": False,
            "RgTools.dll": False,
            "QTGMC.avsi": False,
            "Zs_RF_Shared.avsi": False
        }
        
        for plugin in required_plugins:
            plugin_path = os.path.join(self.plugins_path, plugin)
            required_plugins[plugin] = os.path.exists(plugin_path)
        
        return required_plugins
    
    def get_missing_plugins(self) -> list:
        """Get list of missing required plugins"""
        plugin_status = self.validate_plugins()
        return [name for name, available in plugin_status.items() if not available]