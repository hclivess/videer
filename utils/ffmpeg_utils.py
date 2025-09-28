"""
FFmpeg utilities for videer
Handles FFmpeg command generation and execution
"""

import os
import shutil
import subprocess
from typing import Dict, Any, Optional, List
from config import PRESET_MAPPING, PAR_PRESETS, DAR_PRESETS


def find_ffmpeg() -> Optional[str]:
    """Find FFmpeg executable in system PATH or current directory"""
    # First check if ffmpeg is in system PATH
    ffmpeg_path = shutil.which('ffmpeg')
    if ffmpeg_path:
        return ffmpeg_path
    
    # Then check current directory
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Check Windows executable
    ffmpeg_local = os.path.join(current_dir, 'ffmpeg.exe')
    if os.path.exists(ffmpeg_local):
        return ffmpeg_local
    
    # Check without extension (Linux/Mac)
    ffmpeg_local = os.path.join(current_dir, 'ffmpeg')
    if os.path.exists(ffmpeg_local):
        return ffmpeg_local
    
    return None


def check_ffmpeg_status() -> bool:
    """Check if FFmpeg is available"""
    return find_ffmpeg() is not None


class FFmpegCommandBuilder:
    """Builds FFmpeg commands based on settings"""
    
    def __init__(self, settings: Dict[str, Any]):
        self.settings = settings
        self.ffmpeg_path = find_ffmpeg()
    
    def build_transcode_command(self, input_file: str, output_file: str,
                                transcode_video: bool, transcode_audio: bool) -> List[str]:
        """Build command for transcoding to raw formats"""
        cmd = [self.ffmpeg_path, '-err_detect', 'crccheck+bitstream+buffer', '-hide_banner']
        cmd.extend(['-i', input_file])
        
        preset = PRESET_MAPPING.get(self.settings.get('preset', 'Medium'), 'medium')
        cmd.extend(['-preset', preset])
        cmd.extend(['-map', '0:v', '-map', '0:a?', '-map', '0:s?'])
        
        if transcode_video and transcode_audio:
            cmd.extend(['-c:a', 'pcm_s32le', '-c:v', 'rawvideo'])
        elif transcode_video:
            cmd.extend(['-c:a', 'copy', '-c:v', 'rawvideo'])
        elif transcode_audio:
            cmd.extend(['-c:a', 'pcm_s32le', '-c:v', 'copy'])
        
        cmd.extend(['-c:s', 'copy'])
        cmd.extend([output_file, '-y'])
        
        return cmd
    
    def build_main_command(self, input_file: str, output_file: str,
                          use_avisynth: bool = False) -> List[str]:
        """Build main FFmpeg encoding command"""
        cmd = [self.ffmpeg_path, '-err_detect', 'crccheck+bitstream+buffer', '-hide_banner']
        
        # Hardware acceleration for NVENC
        video_codec = self.settings.get('video_codec', 'libx265')
        if video_codec in ["hevc_nvenc", "h264_nvenc"]:
            cmd.extend(['-hwaccel', 'cuda'])
        
        # Input file
        cmd.extend(['-i', input_file, '-y'])
        
        # Preset
        preset = PRESET_MAPPING.get(self.settings.get('preset', 'Medium'), 'medium')
        cmd.extend(['-preset', preset])
        
        # Mapping
        cmd.extend(['-map', '0:v', '-map', '0:a?', '-map', '0:s?'])
        
        # Stereo downmix if requested
        if self.settings.get('stereo', False):
            cmd.extend(['-ac', '2'])
        
        # Video codec settings
        self._add_video_codec_settings(cmd)
        
        # Audio codec settings
        self._add_audio_codec_settings(cmd)
        
        # Subtitle codec
        cmd.extend(['-c:s', 'copy'])
        
        # PAR/DAR settings
        self._add_aspect_ratio_settings(cmd)
        
        # Corruption fix for TS files
        if self.settings.get('corrupt_fix', False):
            cmd.extend(['-bsf:v', 'h264_mp4toannexb'])
        
        # Metadata
        cmd.extend(['-map_metadata', '0', '-map_chapters', '0'])
        
        # Extra FFmpeg parameters
        extra = self.settings.get('ffmpeg_extras', '').strip()
        if extra:
            cmd.extend(extra.split())
        
        # Add application metadata
        cmd.extend(['-metadata', 'comment=Made with videer'])
        
        # Standard video settings
        cmd.extend(['-bf', '2', '-flags', '+cgop', '-pix_fmt', 'yuv420p'])
        
        # Container-specific options
        output_format = self.settings.get('output_format', 'mkv').lower()
        if output_format == 'mp4':
            cmd.extend(['-movflags', '+faststart'])
        
        # Output format
        format_mapping = {
            'mkv': 'matroska',
            'mp4': 'mp4',
            'avi': 'avi',
            'mov': 'mov',
            'webm': 'webm'
        }
        
        if output_format in format_mapping:
            cmd.extend(['-f', format_mapping[output_format]])
        
        # Output file
        cmd.append(output_file)
        
        return cmd
    
    def _add_video_codec_settings(self, cmd: List[str]):
        """Add video codec settings to command"""
        video_codec = self.settings.get('video_codec', 'libx265')
        
        if video_codec == 'copy':
            cmd.extend(['-c:v', 'copy'])
        else:
            cmd.extend(['-c:v', video_codec])
            
            # Quality settings
            crf = self.settings.get('crf', 23)
            if video_codec in ["hevc_nvenc", "h264_nvenc"]:
                cmd.extend(['-cq', str(crf)])
            elif video_codec != "prores_ks":  # ProRes doesn't use CRF
                cmd.extend(['-crf', str(crf)])
    
    def _add_audio_codec_settings(self, cmd: List[str]):
        """Add audio codec settings to command"""
        audio_codec = self.settings.get('audio_codec', 'aac')
        
        if audio_codec == 'copy':
            cmd.extend(['-c:a', 'copy'])
        else:
            cmd.extend(['-c:a', audio_codec])
            
            # Bitrate settings (not for lossless codecs)
            if audio_codec not in ['flac', 'pcm_s32le']:
                abr = self.settings.get('abr', 256)
                cmd.extend(['-b:a', f'{abr}k'])
    
    def _add_aspect_ratio_settings(self, cmd: List[str]):
        """Add PAR/DAR settings to command"""
        par_handling = self.settings.get('par_handling', 'metadata')
        par_mode = self.settings.get('par_mode', 'auto')
        dar_mode = self.settings.get('dar_mode', 'auto')
        
        # Build video filter chain
        vf_filters = []
        
        # Handle PAR
        if par_mode != 'auto':
            if par_mode == 'custom':
                par_value = self.settings.get('par_custom', '1:1')
            else:
                par_value = PAR_PRESETS.get(par_mode, '1:1')
            
            if par_value and par_value != '1:1':
                if par_handling == 'resample':
                    # Actually resample pixels to square
                    try:
                        par_num, par_den = par_value.split(':')
                        par_ratio = float(par_num) / float(par_den)
                        
                        # Scale width by PAR to get square pixels
                        vf_filters.append(f'scale=iw*{par_ratio:.4f}:ih')
                        vf_filters.append('setsar=1:1')  # Set to square pixels
                    except:
                        pass
                elif par_handling == 'metadata':
                    # Just set SAR metadata
                    cmd.extend(['-aspect', par_value])
        
        # Handle DAR
        if dar_mode != 'auto':
            if dar_mode == 'custom':
                dar_value = self.settings.get('dar_custom', '16:9')
            else:
                dar_value = DAR_PRESETS.get(dar_mode, 'auto')
            
            if dar_value and dar_value != 'auto':
                if par_handling == 'resample' and dar_value:
                    # Resample to specific DAR
                    try:
                        dar_num, dar_den = dar_value.split(':')
                        dar_ratio = float(dar_num) / float(dar_den)
                        
                        # Calculate new dimensions to match DAR
                        # Keep height, adjust width
                        vf_filters.append(f'scale=ih*{dar_ratio:.4f}:ih')
                        vf_filters.append('setdar=' + dar_value)
                    except:
                        pass
                else:
                    # Just set DAR metadata
                    cmd.extend(['-aspect', dar_value])
        
        # Apply video filters if any
        if vf_filters:
            # Add high-quality scaling algorithm
            vf_filters.insert(0, 'scale=flags=lanczos')
            cmd.extend(['-vf', ','.join(vf_filters)])