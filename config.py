"""
Configuration file for videer
Contains all constants and default settings
"""

import os
import multiprocessing

# Application info
APP_NAME = "videer"
APP_VERSION = "3.9"
WINDOW_MIN_WIDTH = 1200
WINDOW_MIN_HEIGHT = 900

# File extensions
VIDEO_EXTENSIONS = [
    '.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', 
    '.m4v', '.mpg', '.mpeg', '.m2ts', '.ts', '.vob', '.3gp'
]

# Codec configurations
VIDEO_CODECS = [
    ("H.264 (x264)", "libx264"),
    ("H.265/HEVC (x265)", "libx265"),
    ("AV1 (SVT-AV1)", "libsvtav1"),
    ("VP9 (libvpx)", "libvpx-vp9"),
    ("NVIDIA H.264 (NVENC)", "h264_nvenc"),
    ("NVIDIA H.265/HEVC (NVENC)", "hevc_nvenc"),
    ("ProRes (HQ)", "prores_ks"),
    ("Raw/Uncompressed", "rawvideo"),
    ("Copy (No Re-encoding)", "copy")
]

AUDIO_CODECS = [
    ("AAC", "aac"),
    ("MP3 (LAME)", "libmp3lame"),
    ("Opus", "libopus"),
    ("AC3", "ac3"),
    ("FLAC (Lossless)", "flac"),
    ("PCM (Uncompressed)", "pcm_s32le"),
    ("Copy (No Re-encoding)", "copy")
]

# Container formats
OUTPUT_FORMATS = ["MKV", "MP4", "AVI", "MOV", "WebM"]

# Encoding presets
ENCODING_PRESETS = [
    "Ultra Fast", "Super Fast", "Very Fast",
    "Faster", "Fast", "Medium", "Slow",
    "Slower", "Very Slow"
]

# x264 / x265 preset names
PRESET_MAPPING = {
    "Ultra Fast": "ultrafast",
    "Super Fast": "superfast",
    "Very Fast": "veryfast",
    "Faster": "faster",
    "Fast": "fast",
    "Medium": "medium",
    "Slow": "slow",
    "Slower": "slower",
    "Very Slow": "veryslow"
}

# NVENC presets p1 (fastest) .. p7 (best quality)
NVENC_PRESET_MAPPING = {
    "Ultra Fast": "p1",
    "Super Fast": "p2",
    "Very Fast": "p3",
    "Faster": "p3",
    "Fast": "p4",
    "Medium": "p4",
    "Slow": "p5",
    "Slower": "p6",
    "Very Slow": "p7"
}

# SVT-AV1 presets 13 (fastest) .. 0 (slowest); 2..12 is the practical range
SVTAV1_PRESET_MAPPING = {
    "Ultra Fast": "12",
    "Super Fast": "11",
    "Very Fast": "10",
    "Faster": "9",
    "Fast": "8",
    "Medium": "6",
    "Slow": "5",
    "Slower": "4",
    "Very Slow": "2"
}

# libvpx-vp9 -cpu-used 5 (fastest) .. 0 (slowest) with -deadline good
VP9_CPU_USED_MAPPING = {
    "Ultra Fast": "5",
    "Super Fast": "5",
    "Very Fast": "4",
    "Faster": "4",
    "Fast": "3",
    "Medium": "2",
    "Slow": "1",
    "Slower": "1",
    "Very Slow": "0"
}

# Deinterlacers: display name -> key
DEINTERLACERS = [
    ("QTGMC (AviSynth+, best quality)", "qtgmc"),
    ("bwdif (FFmpeg, good)", "bwdif"),
    ("yadif (FFmpeg, fast)", "yadif")
]

# Codecs allowed per container (None = anything goes)
CONTAINER_VIDEO_CODECS = {
    "webm": {"libvpx-vp9", "libsvtav1", "copy"},
}
CONTAINER_AUDIO_CODECS = {
    "webm": {"libopus", "copy"},
}

# PAR (Pixel Aspect Ratio) presets
PAR_PRESETS = {
    "Square (1:1)": "1:1",
    "PAL 4:3 (12:11)": "12:11",
    "PAL 16:9 (16:11)": "16:11",
    "NTSC 4:3 (10:11)": "10:11",
    "NTSC 16:9 (40:33)": "40:33",
    "HDV 1080 (4:3)": "4:3",
    "DVCPRO HD 720 (3:2)": "3:2",
    "DVCPRO HD 1080 (3:2)": "3:2",
    "Custom": "custom"
}

# DAR (Display Aspect Ratio) presets
DAR_PRESETS = {
    "Auto": "auto",
    "4:3": "4:3",
    "16:9": "16:9",
    "21:9": "21:9",
    "1:1": "1:1",
    "2.35:1": "2.35:1",
    "2.40:1": "2.40:1",
    "Custom": "custom"
}

# Resolution presets: display name -> target height (None = keep original, 0 = custom)
RESOLUTION_PRESETS = {
    "Original (no scaling)": None,
    "2160p (4K UHD)": 2160,
    "1440p (QHD)": 1440,
    "1080p (Full HD)": 1080,
    "720p (HD)": 720,
    "576p (PAL SD)": 576,
    "480p (NTSC SD)": 480,
    "360p": 360,
    "Custom": 0
}

# Scaling algorithms for FFmpeg's scale filter
SCALE_ALGORITHMS = ["lanczos", "bicubic", "bilinear", "spline", "neighbor"]
DEFAULT_SCALE_ALGORITHM = "lanczos"

# Quality defaults
DEFAULT_CRF = 23
DEFAULT_ABR = 256
DEFAULT_PRESET = "Medium"
DEFAULT_FORMAT = "MKV"

# Thread settings
MAX_THREADS = multiprocessing.cpu_count()
DEFAULT_THREADS = MAX_THREADS

# Logging
LOG_FORMAT = "%(asctime)s [%(threadName)-12.12s] [%(levelname)-5.5s]  %(message)s"

# Processing defaults
DEFAULT_SETTINGS = {
    "video_codec": "libx265",
    "audio_codec": "aac",
    "crf": DEFAULT_CRF,
    "abr": DEFAULT_ABR,
    "preset": DEFAULT_PRESET,
    "output_format": DEFAULT_FORMAT,
    "stereo": False,
    "deinterlace": False,
    "deinterlacer": "qtgmc",
    "tff": False,
    "reduce_fps": False,
    "use_avisynth": False,
    "use_ffms2": False,
    "transcode_video": False,
    "transcode_audio": False,
    "corrupt_fix": False,
    "replace_files": False,
    "delete_source": False,
    "threads": DEFAULT_THREADS,
    "par_mode": "auto",
    "par_value": "1:1",
    "dar_mode": "auto",
    "dar_value": "16:9",
    "resolution_mode": "Original (no scaling)",
    "custom_width": 0,
    "custom_height": 0,
    "no_upscale": True,
    "scale_algorithm": DEFAULT_SCALE_ALGORITHM,
    "calculate_vmaf": False
}

# Path to user defaults file (next to this config file, i.e. app directory)
DEFAULTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "defaults.json")

# Preset configurations
QUALITY_PRESETS = {
    "web": {
        "name": "Web Quality (H.264/AAC)",
        "video_codec": "libx264",
        "audio_codec": "aac",
        "crf": 23,
        "abr": 192,
        "preset": "Fast",
        "output_format": "MP4"
    },
    "hq": {
        "name": "High Quality (H.265/Opus)",
        "video_codec": "libx265",
        "audio_codec": "libopus",
        "crf": 18,
        "abr": 256,
        "preset": "Slow",
        "output_format": "MKV"
    },
    "archive": {
        "name": "Archive (ProRes HQ/PCM)",
        "video_codec": "prores_ks",
        "audio_codec": "pcm_s32le",
        "abr": 512,
        "preset": "Medium",
        "output_format": "MOV"
    },
    "av1": {
        "name": "Modern (AV1/Opus)",
        "video_codec": "libsvtav1",
        "audio_codec": "libopus",
        "crf": 30,
        "abr": 160,
        "preset": "Medium",
        "output_format": "MKV"
    }
}