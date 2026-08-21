"""
FFmpeg utilities for videer
Handles FFmpeg command generation and execution
"""

import os
import shlex
import shutil
from typing import Dict, Any, Optional, List
import subprocess
from utils import childproc
from config import (PRESET_MAPPING, NVENC_PRESET_MAPPING, SVTAV1_PRESET_MAPPING,
                    VP9_CPU_USED_MAPPING, PAR_PRESETS, DAR_PRESETS,
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


_ffprobe_cache: Optional[str] = None


def find_ffprobe() -> Optional[str]:
    """Find ffprobe: PATH, app dir, or next to the ffmpeg binary in use (cached)"""
    global _ffprobe_cache
    if _ffprobe_cache and os.path.exists(_ffprobe_cache):
        return _ffprobe_cache

    candidates = [shutil.which('ffprobe')]
    dirs = [_APP_DIR]
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        dirs.append(os.path.dirname(ffmpeg))
    for d in dirs:
        for name in ('ffprobe.exe', 'ffprobe'):
            candidates.append(os.path.join(d, name))

    for path in candidates:
        if path and os.path.isfile(path):
            _ffprobe_cache = path
            return path
    return None


def probe_duration(filepath: str) -> Optional[float]:
    """Return media duration in seconds via ffprobe, or None if unavailable"""
    ffprobe = find_ffprobe()
    if not ffprobe or not os.path.exists(filepath):
        return None
    try:
        result = childproc.run(
            [ffprobe, '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', filepath],
            text=True, timeout=30)
        value = result.stdout.strip().splitlines()
        return float(value[0]) if value else None
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


class FFmpegCommandBuilder:
    """Builds FFmpeg commands based on settings"""
    
    def __init__(self, settings: Dict[str, Any]):
        self.settings = settings
        self.ffmpeg_path = find_ffmpeg()
    
    # Machine-readable progress on stdout; human stats line suppressed
    PROGRESS_ARGS = ['-progress', 'pipe:1', '-nostats']

    # Subtitle handling per container: codec to use, or None to drop subtitles
    SUBTITLE_CODEC_BY_CONTAINER = {
        'mkv': 'copy',
        'mp4': 'mov_text',
        'mov': 'mov_text',
        'avi': None,
        'webm': None,
    }

    # Matroska statistics tags (mkvmerge/mkvpropedit) that become stale after re-encoding
    STAT_TAGS = ('BPS', 'BPS-eng', 'NUMBER_OF_BYTES', 'NUMBER_OF_BYTES-eng',
                 'NUMBER_OF_FRAMES', 'NUMBER_OF_FRAMES-eng',
                 '_STATISTICS_TAGS', '_STATISTICS_TAGS-eng')

    def _clear_stat_tags(self, cmd: List[str], stream_type: str):
        """Delete stale statistics tags on all streams of the given type (v/a)"""
        for tag in self.STAT_TAGS:
            cmd.extend([f'-metadata:s:{stream_type}', f'{tag}='])

    def _base_command(self, err_detect: bool = True) -> List[str]:
        cmd = [self.ffmpeg_path, '-hide_banner']
        if err_detect:
            cmd.extend(['-err_detect', 'crccheck+bitstream+buffer'])
        cmd.extend(self.PROGRESS_ARGS)
        return cmd

    def build_transcode_command(self, input_file: str, output_file: str,
                                transcode_video: bool, transcode_audio: bool) -> List[str]:
        """Build command for transcoding to raw formats"""
        cmd = self._base_command()
        cmd.extend(['-i', input_file])
        # AVI intermediate: video + audio only (AVI cannot carry text subtitles)
        cmd.extend(['-map', '0:v', '-map', '0:a?', '-sn'])

        if transcode_video and transcode_audio:
            cmd.extend(['-c:a', 'pcm_s32le', '-c:v', 'rawvideo'])
        elif transcode_video:
            cmd.extend(['-c:a', 'copy', '-c:v', 'rawvideo'])
        elif transcode_audio:
            cmd.extend(['-c:a', 'pcm_s32le', '-c:v', 'copy'])

        cmd.extend([output_file, '-y'])
        
        return cmd
    
    def build_main_command(self, input_file: str, output_file: str,
                          use_avisynth: bool = False) -> List[str]:
        """Build main FFmpeg encoding command"""
        cmd = self._base_command()
        
        # Hardware acceleration for NVENC
        video_codec = self.settings.get('video_codec', 'libx265')
        if video_codec in ["hevc_nvenc", "h264_nvenc"]:
            cmd.extend(['-hwaccel', 'cuda'])
        
        # Robustness for damaged transport streams: regenerate PTS, drop corrupt packets
        if self.settings.get('corrupt_fix', False):
            cmd.extend(['-fflags', '+genpts+discardcorrupt'])

        # Input file
        cmd.extend(['-i', input_file, '-y'])

        copy_video = video_codec == 'copy'
        audio_codec = self.settings.get('audio_codec', 'aac')
        output_format = self.settings.get('output_format', 'mkv').lower()

        # Thread budget for decoder/encoder (0 = FFmpeg auto)
        threads = int(self.settings.get('threads') or 0)
        if threads > 0:
            cmd.extend(['-threads', str(threads)])

        # Encoder speed preset (name space differs per encoder)
        self._add_speed_preset(cmd, video_codec)

        # Mapping (subtitles only where the container can carry them)
        cmd.extend(['-map', '0:v', '-map', '0:a?'])
        subtitle_codec = self.SUBTITLE_CODEC_BY_CONTAINER.get(output_format, 'copy')
        if subtitle_codec:
            cmd.extend(['-map', '0:s?'])
        else:
            cmd.append('-sn')

        # Stereo downmix (only possible when audio is re-encoded)
        if self.settings.get('stereo', False) and audio_codec != 'copy':
            cmd.extend(['-ac', '2'])

        # Video codec settings
        self._add_video_codec_settings(cmd)

        # Audio codec settings
        self._add_audio_codec_settings(cmd)

        # Subtitle codec
        if subtitle_codec:
            cmd.extend(['-c:s', subtitle_codec])

        # Video filters (resolution + PAR/DAR) and DAR metadata.
        # Filters cannot be applied to a copied stream, so skip them entirely then.
        if not copy_video:
            vf_filters = self.build_video_filters()
            if vf_filters:
                cmd.extend(['-vf', ','.join(vf_filters)])
            self._add_dar_metadata(cmd)

        # Metadata: keep global tags/chapters, but drop per-stream statistics tags
        # (BPS, NUMBER_OF_BYTES, ...) written by mkvmerge for streams we re-encode —
        # otherwise media tools show the *source* bitrate for the new stream.
        cmd.extend(['-map_metadata', '0', '-map_chapters', '0'])
        if not copy_video:
            self._clear_stat_tags(cmd, 'v')
        if audio_codec != 'copy':
            self._clear_stat_tags(cmd, 'a')

        # Extra FFmpeg parameters
        extra = self.settings.get('ffmpeg_extras', '').strip()
        if extra:
            cmd.extend(shlex.split(extra, posix=(os.name != 'nt')))

        # Add application metadata
        cmd.extend(['-metadata', 'comment=Made with videer'])

        # Standard video settings (not applicable to stream copy / raw / ProRes)
        if video_codec in ('libx264', 'libx265', 'h264_nvenc', 'hevc_nvenc'):
            cmd.extend(['-bf', '2', '-flags', '+cgop'])
        if video_codec not in ('copy', 'rawvideo', 'prores_ks'):
            cmd.extend(['-pix_fmt', 'yuv420p'])

        # Container-specific options
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
    
    def _add_speed_preset(self, cmd: List[str], video_codec: str):
        """Add the encoder-specific speed/quality trade-off option"""
        preset_name = self.settings.get('preset', 'Medium')
        if video_codec in ('libx264', 'libx265'):
            cmd.extend(['-preset', PRESET_MAPPING.get(preset_name, 'medium')])
        elif video_codec in ('h264_nvenc', 'hevc_nvenc'):
            cmd.extend(['-preset', NVENC_PRESET_MAPPING.get(preset_name, 'p4'),
                        '-tune', 'hq', '-rc', 'vbr'])
        elif video_codec == 'libsvtav1':
            cmd.extend(['-preset', SVTAV1_PRESET_MAPPING.get(preset_name, '6')])
        elif video_codec == 'libvpx-vp9':
            cmd.extend(['-deadline', 'good',
                        '-cpu-used', VP9_CPU_USED_MAPPING.get(preset_name, '2'),
                        '-row-mt', '1'])

    def _add_video_codec_settings(self, cmd: List[str]):
        """Add video codec settings to command"""
        video_codec = self.settings.get('video_codec', 'libx265')

        if video_codec == 'copy':
            cmd.extend(['-c:v', 'copy'])
            return

        cmd.extend(['-c:v', video_codec])
        crf = str(self.settings.get('crf', 23))

        if video_codec in ("hevc_nvenc", "h264_nvenc"):
            cmd.extend(['-cq', crf, '-b:v', '0'])
        elif video_codec == 'libvpx-vp9':
            cmd.extend(['-crf', crf, '-b:v', '0'])   # constant quality mode
        elif video_codec == 'prores_ks':
            cmd.extend(['-profile:v', '3'])           # ProRes 422 HQ; no CRF
        elif video_codec == 'rawvideo':
            pass
        else:
            cmd.extend(['-crf', crf])
    
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

    def _deinterlace_filter(self) -> Optional[str]:
        """yadif/bwdif filter when deinterlacing without AviSynth+"""
        if not self.settings.get('deinterlace', False):
            return None
        deinterlacer = self.settings.get('deinterlacer', 'qtgmc')
        if deinterlacer == 'qtgmc' or self.settings.get('use_avisynth', False):
            return None
        mode = 'send_frame' if self.settings.get('reduce_fps', False) else 'send_field'
        parity = 'tff' if self.settings.get('tff', False) else 'bff'
        name = 'bwdif' if deinterlacer == 'bwdif' else 'yadif'
        return f'{name}=mode={mode}:parity={parity}'

    def build_video_filters(self) -> List[str]:
        """Return the list of -vf filters implied by the current settings"""
        filters: List[str] = []
        par_handling = self.settings.get('par_handling', 'metadata')
        par = self._resolved_par()
        dar = self._resolved_dar()

        # FFmpeg-side deinterlacing (QTGMC is done in the AviSynth script instead)
        deint = self._deinterlace_filter()
        if deint:
            filters.append(deint)

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
        cmd = self._base_command(err_detect=False)
        cmd.extend(['-i', encoded_file])   # distorted
        cmd.extend(['-i', original_file])  # reference
        # Align timestamps and scale the encode back to the reference size so
        # VMAF works after resolution/PAR changes.
        graph = ('[0:v]setpts=PTS-STARTPTS[d];[1:v]setpts=PTS-STARTPTS[r];'
                 '[d][r]scale2ref=flags=bicubic[ds][rs];[ds][rs]libvmaf')
        cmd.extend(['-lavfi', graph])
        cmd.extend(['-f', 'null', '-'])
        return cmd