"""
FFmpeg utilities for videer
Handles FFmpeg command generation and execution
"""

import json
import os
import re
import shlex
import shutil
from typing import Dict, Any, Optional, List, Tuple
import subprocess
from utils import childproc
from config import (PRESET_MAPPING, NVENC_PRESET_MAPPING, SVTAV1_PRESET_MAPPING,
                    VP9_CPU_USED_MAPPING, LIBAOM_CPU_USED_MAPPING, VVENC_PRESET_MAPPING,
                    NVENC_ENCODERS, PAR_PRESETS, DAR_PRESETS,
                    RESOLUTION_PRESETS, DEFAULT_SCALE_ALGORITHM,
                    QUALITY_METRICS, DEFAULT_QUALITY_METRIC, APP_DIR)


_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ffmpeg_cache: Optional[str] = None

# Where a build fetched through Tools > FFmpeg Features lands. It comes first in the search: putting it there
# was a deliberate act, and the reason for doing it is usually that the FFmpeg already on the PATH is the one
# that cannot do the job. Deleting the folder goes back to the system copy.
MANAGED_BIN_DIR = os.path.join(APP_DIR, "ffmpeg", "bin")


def _managed(name: str) -> List[str]:
    return [os.path.join(MANAGED_BIN_DIR, f"{name}.exe"), os.path.join(MANAGED_BIN_DIR, name)]


def find_ffmpeg(refresh: bool = False) -> Optional[str]:
    """Find FFmpeg: the build videer installed, then the system PATH, then the application directory"""
    global _ffmpeg_cache
    if _ffmpeg_cache and not refresh and os.path.exists(_ffmpeg_cache):
        return _ffmpeg_cache

    candidates = _managed('ffmpeg') + [shutil.which('ffmpeg')]
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

    candidates = _managed('ffprobe') + [shutil.which('ffprobe')]
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


def forget_ffmpeg() -> None:
    """Drop everything cached about the FFmpeg in use, so a newly installed one is picked up at once"""
    global _ffmpeg_cache, _ffprobe_cache, _filters_cache, _encoders_cache
    _ffmpeg_cache = None
    _ffprobe_cache = None
    _filters_cache = None
    _encoders_cache = None


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


# Encoders whose quality is a single CRF/CQ/QP number — the ones a quality search can tune. ProRes, rawvideo
# and stream copy have no such knob, so there is nothing for the search to move.
CRF_ENCODERS = ('libx264', 'libx265', 'libvvenc', 'h264_nvenc', 'hevc_nvenc', 'av1_nvenc',
                'libsvtav1', 'libaom-av1', 'libvpx-vp9')

# Encoders that take their input at 10 bits only. Asking for yuv420p makes FFmpeg print a warning and pick
# this itself; asking for it outright keeps the log clean and the intention visible in the command.
TEN_BIT_ONLY_ENCODERS = {'libvvenc': 'yuv420p10le'}


def output_pixel_format(video_codec: str) -> str:
    return TEN_BIT_ONLY_ENCODERS.get(video_codec, 'yuv420p')


_filters_cache: Optional[set] = None


def ffmpeg_filters(refresh: bool = False) -> set:
    """Names of the filters this FFmpeg build has (cached). Empty when the build could not be asked."""
    global _filters_cache
    if _filters_cache is not None and not refresh:
        return _filters_cache

    names = set()
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        try:
            result = childproc.run([ffmpeg, '-hide_banner', '-filters'], text=True, timeout=30)
            for line in (result.stdout or '').splitlines():
                # " TS. name  VV->V  description"; the legend lines above the table have '=' where the name is
                parts = line.split()
                if len(parts) >= 3 and parts[1] != '=' and '->' in parts[2]:
                    names.add(parts[1])
        except (subprocess.SubprocessError, OSError):
            pass
    _filters_cache = names
    return names


_encoders_cache: Optional[set] = None


def ffmpeg_encoders(refresh: bool = False) -> set:
    """Names of the encoders this FFmpeg build has (cached). Empty when the build could not be asked."""
    global _encoders_cache
    if _encoders_cache is not None and not refresh:
        return _encoders_cache

    names = set()
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        try:
            result = childproc.run([ffmpeg, '-hide_banner', '-encoders'], text=True, timeout=30)
            for line in (result.stdout or '').splitlines():
                # " V....D libvvenc  description": six flag characters, then the name. The legend above the
                # table puts one flag character and '=' where those would be.
                parts = line.split()
                if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in 'VAS' and parts[1] != '=':
                    names.add(parts[1])
        except (subprocess.SubprocessError, OSError):
            pass
    _encoders_cache = names
    return names


def ffmpeg_has_encoder(name: str) -> bool:
    """
    Whether the FFmpeg in use provides an encoder. Same rule as for filters: an FFmpeg that could not be asked
    answers True, so a failed probe never disables anything — the encode itself will say what is wrong.
    """
    encoders = ffmpeg_encoders()
    return name in encoders if encoders else True


def ffmpeg_has_filter(name: str) -> bool:
    """
    Whether the FFmpeg in use provides a filter. An FFmpeg that could not be asked at all (missing, or
    -filters failed) answers True: better to try the command and report FFmpeg's own error than to disable a
    feature on the strength of a failed probe.
    """
    filters = ffmpeg_filters()
    return name in filters if filters else True


def has_audio_stream(filepath: str) -> bool:
    """
    Whether the file carries any audio at all.

    Unknown counts as yes: if ffprobe cannot be asked, the caller should keep whatever audio handling it
    would have used, rather than silently dropping the audio of a file that has some.
    """
    ffprobe = find_ffprobe()
    if not ffprobe or not os.path.exists(filepath):
        return True
    try:
        result = childproc.run(
            [ffprobe, '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=index',
             '-of', 'csv=p=0', filepath], text=True, timeout=30)
    except (subprocess.SubprocessError, OSError):
        return True
    return bool((result.stdout or '').strip())


# Subtitle codecs that carry text, and can therefore be converted into whatever text format the target
# container wants. Everything else is a picture (VOBSUB, PGS, DVB) and can only be copied or dropped:
# FFmpeg converts text to text and bitmap to bitmap, never across.
TEXT_SUBTITLE_CODECS = frozenset((
    'subrip', 'srt', 'text', 'ass', 'ssa', 'mov_text', 'webvtt', 'ttml', 'sami', 'realtext',
    'subviewer', 'subviewer1', 'microdvd', 'mpl2', 'pjs', 'jacosub', 'stl', 'vplayer', 'eia_608'))


# "  Stream #0:2(eng): Subtitle: mov_text (tx3g / 0x67337874), 1920x1080" — how ffmpeg -i describes a
# subtitle stream when there is no ffprobe to ask.
_STREAM_SUBTITLE_RE = re.compile(r'Stream #\d+:\d+.*?: Subtitle: ([A-Za-z0-9_]+)')


def probe_subtitle_codecs(filepath: str) -> List[str]:
    """
    Codec name of every subtitle stream, in stream order. Empty when the file has none, is not a media file
    (an AviSynth script), or could not be examined at all — in which case the caller keeps whatever it would
    have done anyway rather than acting on a failed probe.

    ffprobe answers this best, but it is a separate executable and not everyone who has ffmpeg has it on
    PATH. Rather than let a missing helper decide whether an encode survives, fall back to reading the stream
    list `ffmpeg -i` prints about any input it opens.
    """
    if not os.path.exists(filepath):
        return []

    ffprobe = find_ffprobe()
    if ffprobe:
        try:
            result = childproc.run(
                [ffprobe, '-v', 'error', '-select_streams', 's', '-show_entries', 'stream=codec_name',
                 '-of', 'csv=p=0', filepath], text=True, encoding='utf-8', errors='replace', timeout=30)
            names = [line.strip() for line in (result.stdout or '').splitlines() if line.strip()]
            if names or result.returncode == 0:
                return names
        except (subprocess.SubprocessError, OSError):
            pass

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return []
    try:
        # No output file: ffmpeg prints what it found and exits non-zero. That listing is the answer.
        result = childproc.run([ffmpeg, '-hide_banner', '-i', filepath],
                               text=True, encoding='utf-8', errors='replace', timeout=30)
    except (subprocess.SubprocessError, OSError):
        return []
    return _STREAM_SUBTITLE_RE.findall((result.stderr or '') + (result.stdout or ''))


def probe_media_info(filepath: str) -> Dict[str, Any]:
    """Duration, size and first-video-stream properties, best effort — every field may be None."""
    info: Dict[str, Any] = {'duration': None, 'size': None, 'width': None, 'height': None,
                            'video_bitrate': None, 'total_bitrate': None, 'codec': None, 'fps': None}
    try:
        info['size'] = os.path.getsize(filepath)
    except OSError:
        pass

    ffprobe = find_ffprobe()
    if not ffprobe or not os.path.exists(filepath):
        return info

    try:
        result = childproc.run(
            [ffprobe, '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'format=duration,bit_rate:'
                              'stream=width,height,bit_rate,codec_name,avg_frame_rate',
             '-of', 'json', filepath],
            text=True, timeout=30)
        data = json.loads(result.stdout or '{}')
    except (subprocess.SubprocessError, ValueError, OSError):
        return info

    def number(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    fmt = data.get('format') or {}
    info['duration'] = number(fmt.get('duration'))
    info['total_bitrate'] = number(fmt.get('bit_rate'))

    streams = data.get('streams') or []
    if streams:
        stream = streams[0]
        info['codec'] = stream.get('codec_name')
        info['width'] = int(stream['width']) if str(stream.get('width', '')).isdigit() else None
        info['height'] = int(stream['height']) if str(stream.get('height', '')).isdigit() else None
        info['video_bitrate'] = number(stream.get('bit_rate'))
        rate = (stream.get('avg_frame_rate') or '').split('/')
        if len(rate) == 2 and number(rate[0]) and number(rate[1]):
            info['fps'] = number(rate[0]) / number(rate[1])

    # A container that stores no per-stream bitrate (Matroska usually does not) still tells us the total
    if info['video_bitrate'] is None and info['total_bitrate'] is None and info['size'] and info['duration']:
        info['total_bitrate'] = info['size'] * 8 / info['duration']
    return info


class FFmpegCommandBuilder:
    """Builds FFmpeg commands based on settings"""
    
    def __init__(self, settings: Dict[str, Any]):
        self.settings = settings
        self.ffmpeg_path = find_ffmpeg()
        # Subtitle streams the last built command had to leave behind: [(stream index, codec name)]
        self.dropped_subtitles: List[Tuple[int, str]] = []
        # Set to 'convert' or 'drop' to overrule the subtitle plan after a muxer has rejected what it chose
        self.subtitle_override: Optional[str] = None
    
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

    # What each container will actually accept as a copied subtitle stream, and which text format to convert
    # to when it will not. Matroska takes nearly everything *except* MP4's mov_text, which is exactly what a
    # subtitled MP4 source carries — copying it in aborts the whole encode with "Subtitle codec 94213 is not
    # supported". MP4 and MOV take only mov_text, so a picture-based subtitle track cannot go in at all.
    CONTAINER_SUBTITLES = {
        'mkv': {'copyable': frozenset(('subrip', 'srt', 'text', 'ass', 'ssa', 'webvtt', 'dvd_subtitle',
                                       'hdmv_pgs_subtitle', 'hdmv_text_subtitle', 'dvb_subtitle',
                                       'arib_caption')),
                'text': 'srt'},
        'mp4': {'copyable': frozenset(('mov_text',)), 'text': 'mov_text'},
        'mov': {'copyable': frozenset(('mov_text',)), 'text': 'mov_text'},
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
            # Repair inverts the usual stance: normally a damaged packet should be reported and refused, but
            # when the damage is the whole reason for the run, refusing it means refusing to repair anything.
            mode = 'ignore_err' if self.settings.get('repair_mode') else 'crccheck+bitstream+buffer'
            cmd.extend(['-err_detect', mode])
        cmd.extend(self.PROGRESS_ARGS)
        return cmd

    def _subtitle_plan(self, input_file: str, output_format: str) -> Tuple[List[str], List[str]]:
        """
        How the source's subtitle streams reach the output: the -map arguments and the -c:s arguments.

        Copying subtitles is right whenever the container accepts them, and fatal when it does not — FFmpeg
        refuses to write the header and the file is lost, subtitles being the least of what the user wanted.
        So each source stream is asked about individually: copy where the container takes it, convert where
        the container wants another text format, and drop where a picture-based track cannot go in at all.

        A source that cannot be probed (an AviSynth script, no ffprobe) keeps the old blanket behaviour: it is
        better to run the command and let FFmpeg speak than to drop subtitles on the strength of a failed
        probe.
        """
        self.dropped_subtitles = []
        configured = self.SUBTITLE_CODEC_BY_CONTAINER.get(output_format, 'copy')
        if not configured or self.subtitle_override == 'drop':
            return ['-sn'], []

        rules = self.CONTAINER_SUBTITLES.get(output_format)
        if self.subtitle_override == 'convert':
            # The muxer refused what was chosen and we may not know why: convert everything text-shaped into
            # the container's own format, which is the one thing it is guaranteed to accept.
            return ['-map', '0:s?'], ['-c:s', rules['text'] if rules else configured]

        codecs = probe_subtitle_codecs(input_file) if rules else []
        if not codecs:
            return ['-map', '0:s?'], ['-c:s', configured]

        keep: List[int] = []
        targets: List[str] = []
        for index, name in enumerate(codecs):
            if name in rules['copyable'] and (configured == 'copy' or name == configured):
                target = 'copy'
            elif name in TEXT_SUBTITLE_CODECS:
                target = rules['text']
            else:
                # A picture this container cannot hold. The alternative is failing the whole file, but the
                # caller is told, because silently losing a subtitle track is its own kind of damage.
                self.dropped_subtitles.append((index, name))
                continue
            keep.append(index)
            targets.append(target)

        if not keep:
            return ['-sn'], []

        if len(keep) == len(codecs):
            mapping = ['-map', '0:s?']
        else:
            mapping = [arg for index in keep for arg in ('-map', f'0:s:{index}')]

        if len(set(targets)) == 1:
            return mapping, ['-c:s', targets[0]]
        return mapping, [arg for position, target in enumerate(targets)
                         for arg in (f'-c:s:{position}', target)]

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
        if video_codec in NVENC_ENCODERS:
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

        # Mapping (subtitles only where the container can carry them, and only in a form it accepts)
        cmd.extend(['-map', '0:v', '-map', '0:a?'])
        subtitle_map, subtitle_codec_args = self._subtitle_plan(input_file, output_format)
        cmd.extend(subtitle_map)

        # Stereo downmix (only possible when audio is re-encoded)
        if self.settings.get('stereo', False) and audio_codec != 'copy':
            cmd.extend(['-ac', '2'])

        # Video codec settings
        self._add_video_codec_settings(cmd)

        # Audio codec settings
        self._add_audio_codec_settings(cmd)

        # Subtitle codec
        cmd.extend(subtitle_codec_args)

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

        # Standard video settings (not applicable to stream copy / raw / ProRes).
        # NVENC gets its B-frame count from _add_nvenc_quality_settings instead.
        if video_codec in ('libx264', 'libx265'):
            cmd.extend(['-bf', '2', '-flags', '+cgop'])
        elif video_codec in NVENC_ENCODERS:
            cmd.extend(['-flags', '+cgop'])
        if video_codec not in ('copy', 'rawvideo', 'prores_ks'):
            cmd.extend(['-pix_fmt', output_pixel_format(video_codec)])

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
        elif video_codec in NVENC_ENCODERS:
            cmd.extend(['-preset', NVENC_PRESET_MAPPING.get(preset_name, 'p4'),
                        '-tune', 'hq', '-rc', 'vbr'])
            self._add_nvenc_quality_settings(cmd)
        elif video_codec == 'libsvtav1':
            cmd.extend(['-preset', SVTAV1_PRESET_MAPPING.get(preset_name, '6')])
        elif video_codec == 'libaom-av1':
            # row-mt is what lets libaom use more than a couple of cores; it is off by default
            cmd.extend(['-cpu-used', LIBAOM_CPU_USED_MAPPING.get(preset_name, '4'), '-row-mt', '1'])
        elif video_codec == 'libvvenc':
            cmd.extend(['-preset', VVENC_PRESET_MAPPING.get(preset_name, 'medium')])
        elif video_codec == 'libvpx-vp9':
            cmd.extend(['-deadline', 'good',
                        '-cpu-used', VP9_CPU_USED_MAPPING.get(preset_name, '2'),
                        '-row-mt', '1'])

    def _add_nvenc_quality_settings(self, cmd: List[str]):
        """
        The quality knobs NVENC leaves switched off by default. FFmpeg ships `-rc-lookahead 0`,
        `-spatial-aq false`, `-temporal-aq false` and `-multipass disabled`, so the plain
        `-preset pN -tune hq -rc vbr -cq N` setup encodes with no lookahead, no adaptive quantisation and a
        single pass — which is where most of NVENC's reputation for soft, detail-smeared output comes from.
        These are the settings that recover it; every one of them costs encoding speed, so each is a toggle.
        """
        lookahead = int(self.settings.get('nvenc_lookahead', 32) or 0)
        if lookahead > 0:
            cmd.extend(['-rc-lookahead', str(lookahead)])

        if self.settings.get('nvenc_aq', True):
            cmd.extend(['-spatial-aq', '1',
                        '-aq-strength', str(int(self.settings.get('nvenc_aq_strength', 8)))])
            # Temporal AQ distributes bits across the lookahead window; without one it does nothing and
            # older drivers reject the combination outright.
            if lookahead > 0:
                cmd.extend(['-temporal-aq', '1'])

        multipass = self.settings.get('nvenc_multipass', 'fullres')
        if multipass in ('qres', 'fullres'):
            cmd.extend(['-multipass', multipass])

        bframes = int(self.settings.get('nvenc_bframes', 3))
        cmd.extend(['-bf', str(bframes)])
        # B-frames as reference needs at least two of them, and the hardware for it arrived with Turing —
        # older cards report the capability as unsupported and the encoder refuses to open.
        if bframes >= 2 and self.settings.get('nvenc_b_ref', True):
            cmd.extend(['-b_ref_mode', 'middle'])

    def _add_video_codec_settings(self, cmd: List[str]):
        """Add video codec settings to command"""
        video_codec = self.settings.get('video_codec', 'libx265')

        if video_codec == 'copy':
            cmd.extend(['-c:v', 'copy'])
            return

        cmd.extend(['-c:v', video_codec])
        crf = str(self.settings.get('crf', 23))

        if video_codec in NVENC_ENCODERS:
            cmd.extend(['-cq', crf, '-b:v', '0'])
        elif video_codec in ('libvpx-vp9', 'libaom-av1'):
            cmd.extend(['-crf', crf, '-b:v', '0'])   # constant quality mode
        elif video_codec == 'libvvenc':
            cmd.extend(['-qp', crf])                  # VVenC has no CRF; its constant-quality knob is the QP
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

    def build_sample_encode_command(self, input_file: str, output_file: str,
                                    start: float, duration: float) -> List[str]:
        """
        Encode one short segment of the source with the settings currently selected — the probe a quality
        search measures. Everything that moves quality is kept (encoder, speed preset, CQ/CRF, NVENC options,
        the filter chain); everything that does not is dropped, because each probe is encoded several times:
        no audio, no subtitles, no metadata, and always Matroska regardless of the chosen container.
        """
        cmd = self._base_command(err_detect=False)
        video_codec = self.settings.get('video_codec', 'libx265')
        if video_codec in NVENC_ENCODERS:
            cmd.extend(['-hwaccel', 'cuda'])

        # -ss before -i: FFmpeg seeks to the preceding keyframe and decodes forward to the exact timestamp,
        # so the segment starts on the same frame the reference side of the metric will start on.
        cmd.extend(['-ss', f'{start:.3f}', '-t', f'{duration:.3f}', '-i', input_file, '-y'])

        threads = int(self.settings.get('threads') or 0)
        if threads > 0:
            cmd.extend(['-threads', str(threads)])

        self._add_speed_preset(cmd, video_codec)
        cmd.extend(['-map', '0:v:0', '-an', '-sn', '-dn'])
        self._add_video_codec_settings(cmd)

        vf_filters = self.build_video_filters()
        if vf_filters:
            cmd.extend(['-vf', ','.join(vf_filters)])

        if video_codec in ('libx264', 'libx265'):
            cmd.extend(['-bf', '2', '-flags', '+cgop'])
        elif video_codec in NVENC_ENCODERS:
            cmd.extend(['-flags', '+cgop'])
        cmd.extend(['-pix_fmt', output_pixel_format(video_codec)])

        cmd.extend(['-f', 'matroska', output_file])
        return cmd

    def build_metric_command(self, encoded_file: str, original_file: str, metric: str = 'vmaf',
                             start: Optional[float] = None, duration: Optional[float] = None,
                             threads: int = 0, log_name: Optional[str] = None) -> List[str]:
        """
        Compare an encode against its source with a full-reference metric. start/duration cut the same
        segment out of the reference that the encode was made from; without them the whole file is scored.

        log_name is a *bare filename*, not a path: the per-frame log is the only way to pool anything other
        than the mean, and a filter option containing a path would have to be escaped for the filtergraph
        parser — on Windows that means escaping both the drive colon and every backslash. Running FFmpeg with
        its working directory set to where the log should land removes the problem instead of encoding it.
        """
        spec = QUALITY_METRICS.get(metric) or QUALITY_METRICS[DEFAULT_QUALITY_METRIC]

        cmd = self._base_command(err_detect=False)
        cmd.extend(['-i', encoded_file])   # distorted
        if start is not None:
            cmd.extend(['-ss', f'{start:.3f}'])
        if duration is not None:
            cmd.extend(['-t', f'{duration:.3f}'])
        cmd.extend(['-i', original_file])  # reference

        options = dict(spec.get('args') or {})
        if spec['family'] == 'libvmaf':
            if threads > 0:
                # libvmaf is single-threaded by default and is then slower than the encode it is scoring
                options['n_threads'] = str(threads)
            if log_name:
                options['log_path'] = log_name
                options['log_fmt'] = 'csv'      # a tenth of the size of the JSON log, same per-frame rows
        elif log_name:
            options['stats_file'] = log_name

        filter_spec = spec['filter']
        if options:
            filter_spec += '=' + ':'.join(f'{key}={value}' for key, value in options.items())

        # The reference gets the same filter chain the encode was made with. Without that the score is not
        # about the encoder at all: deinterlacing in send_field mode gives the encode two frames for every
        # one of the source's, so the metric compares interpolated fields against whole frames that are half
        # a frame away in time, and reads 65 where the encode is worth 99. No CRF can fix that, so a search
        # walks to the bottom of its range on every file. Whatever the filters change, they change on both
        # sides, and what is left to measure is what the encoder lost.
        reference_chain = ','.join(self.build_video_filters())
        if reference_chain:
            reference_chain += ','

        # Align timestamps and scale the encode back to the reference size so
        # the comparison works after resolution/PAR changes.
        graph = ('[0:v]setpts=PTS-STARTPTS[d];'
                 f'[1:v]{reference_chain}setpts=PTS-STARTPTS[r];'
                 f'[d][r]scale2ref=flags=bicubic[ds][rs];[ds][rs]{filter_spec}')
        cmd.extend(['-lavfi', graph])
        # Nothing but the metric is wanted: without -an FFmpeg still selects an audio stream and decodes it
        # into the null muxer for the whole comparison.
        cmd.extend(['-an', '-sn', '-f', 'null', '-'])
        return cmd

    def build_vmaf_command(self, encoded_file, original_file):
        """Build FFmpeg command to calculate VMAF score for a finished encode"""
        return self.build_metric_command(encoded_file, original_file, 'vmaf')
