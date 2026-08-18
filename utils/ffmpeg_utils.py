"""
FFmpeg utilities for videer
Handles FFmpeg command generation and execution
"""

import os
import shlex
import shutil
from typing import Dict, Any, Optional, List
from config import (PRESET_MAPPING, PAR_PRESETS, DAR_PRESETS,
                    RESOLUTION_PRESETS, DEFAULT_SCALE_ALGORITHM)


_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ffmpeg_cache: Optional[str] = None


def find_ffmpeg(refresh: bool = False) -> Optional[str]:
    """Find FFmpeg executable in system PATH or the application directory (cached)"""
    global _ffmpeg_cache
    if _ffmpeg_cache and not refresh and os.path.exists(_ffmpeg_cache):
        return _ffmpeg_cache

    candidates = [shutil.which('ffmpeg')]
    for name in ('ffmpeg.exe', 'ffmpeg'):
        candidates.append(os.path.join(_APP_DIR, name))

    for path in candidates:
        if path and os.path.isfile(path):
            _ffmpeg_cache = path
            return path

    _ffmpeg_cache = None
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

        copy_video = video_codec == 'copy'

        # Encoder speed preset (only meaningful for x264/x265/NVENC)
        if video_codec in ('libx264', 'libx265', 'h264_nvenc', 'hevc_nvenc'):
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

        # Video filters (resolution + PAR/DAR) and DAR metadata.
        # Filters cannot be applied to a copied stream, so skip them entirely then.
        if not copy_video:
            vf_filters = self.build_video_filters()
            if vf_filters:
                cmd.extend(['-vf', ','.join(vf_filters)])
            self._add_dar_metadata(cmd)

        # Corruption fix for TS files
        if self.settings.get('corrupt_fix', False):
            cmd.extend(['-bsf:v', 'h264_mp4toannexb'])

        # Metadata
        cmd.extend(['-map_metadata', '0', '-map_chapters', '0'])

        # Extra FFmpeg parameters
        extra = self.settings.get('ffmpeg_extras', '').strip()
        if extra:
            cmd.extend(shlex.split(extra, posix=(os.name != 'nt')))

        # Add application metadata
        cmd.extend(['-metadata', 'comment=Made with videer'])

        # Standard video settings (not applicable to stream copy / raw / ProRes)
        if video_codec not in ('copy', 'rawvideo', 'prores_ks'):
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
    
    # ------------------------------------------------------------------
    # Video filter chain
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_ratio(value: Optional[str]) -> Optional[float]:
        """Parse 'num:den' (or 'num/den') into a float; None if invalid or 'auto'"""
        if not value or value in ('auto', 'custom'):
            return None
        try:
            num, den = value.replace('/', ':').split(':')
            num, den = float(num), float(den)
            return num / den if den else None
        except (ValueError, AttributeError):
            return None

    def _resolved_par(self) -> Optional[str]:
        """PAR string the user asked for, or None for auto / 1:1"""
        mode = self.settings.get('par_mode', 'auto')
        if mode == 'Custom':
            value = self.settings.get('par_custom', '')
        else:
            value = self.settings.get('par_value') or PAR_PRESETS.get(mode, '1:1')
        if self._parse_ratio(value) is None or value.strip() == '1:1':
            return None
        return value.strip()

    def _resolved_dar(self) -> Optional[str]:
        """DAR string the user asked for, or None for auto"""
        mode = self.settings.get('dar_mode', 'auto')
        if mode == 'Custom':
            value = self.settings.get('dar_custom', '')
        else:
            value = self.settings.get('dar_value') or DAR_PRESETS.get(mode, 'auto')
        return value.strip() if self._parse_ratio(value) is not None else None

    def _resolution_filter(self) -> Optional[str]:
        """Build a scale filter for the requested output resolution, or None"""
        mode = self.settings.get('resolution_mode', 'Original (no scaling)')
        target_height = RESOLUTION_PRESETS.get(mode)
        no_upscale = self.settings.get('no_upscale', True)

        if target_height is None:
            return None  # keep original

        if target_height == 0:  # custom
            width = int(self.settings.get('custom_width') or 0)
            height = int(self.settings.get('custom_height') or 0)
            if width <= 0 and height <= 0:
                return None
            if no_upscale:
                w_expr = f"'min({width},iw)'" if width > 0 else '-2'
                h_expr = f"'min({height},ih)'" if height > 0 else '-2'
            else:
                w_expr = str(width) if width > 0 else '-2'
                h_expr = str(height) if height > 0 else '-2'
            return f'scale={w_expr}:{h_expr}'

        if no_upscale:
            return f"scale=-2:'min({target_height},ih)'"
        return f'scale=-2:{target_height}'

    def build_video_filters(self) -> List[str]:
        """Return the list of -vf filters implied by the current settings"""
        filters: List[str] = []
        par_handling = self.settings.get('par_handling', 'metadata')
        par = self._resolved_par()
        dar = self._resolved_dar()

        # PAR handling
        if par:
            if par_handling == 'resample':
                ratio = self._parse_ratio(par)
                filters.append(f'scale=iw*{ratio:.4f}:ih')
                filters.append('setsar=1:1')
            elif par_handling == 'metadata':
                filters.append(f'setsar={par}')

        # DAR resample (metadata-only DAR is handled by -aspect)
        if dar and par_handling == 'resample':
            ratio = self._parse_ratio(dar)
            filters.append(f'scale=ih*{ratio:.4f}:ih')
            filters.append(f'setdar={dar}')

        # Output resolution (applied last so it operates on square pixels)
        res_filter = self._resolution_filter()
        if res_filter:
            filters.append(res_filter)

        # Attach the chosen scaling algorithm to every scale filter
        algo = self.settings.get('scale_algorithm', DEFAULT_SCALE_ALGORITHM)
        filters = [f'{f}:flags={algo}' if f.startswith('scale=') else f for f in filters]
        return filters

    def _add_dar_metadata(self, cmd: List[str]):
        """Set DAR via -aspect when only metadata is requested"""
        dar = self._resolved_dar()
        if dar and self.settings.get('par_handling', 'metadata') != 'resample':
            cmd.extend(['-aspect', dar])

    def build_vmaf_command(self, encoded_file, original_file):
        """Build FFmpeg command to calculate VMAF score"""
        cmd = [self.ffmpeg_path, '-hide_banner']
        cmd.extend(['-i', encoded_file])   # distorted
        cmd.extend(['-i', original_file])  # reference
        cmd.extend(['-lavfi', 'libvmaf'])
        cmd.extend(['-f', 'null', '-'])
        return cmd