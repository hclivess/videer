"""
Configuration file for videer
Contains all constants and default settings
"""

import multiprocessing

# Application info
APP_NAME = "videer"
APP_VERSION = "2.1"
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
    ("NVIDIA H.264 (NVENC)", "h264_nvenc"),
    ("NVIDIA H.265/HEVC (NVENC)", "hevc_nvenc"),
    ("ProRes", "prores_ks"),
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
    "tff": False,
    "reduce_fps": False,
    "use_avisynth": False,
    "use_ffms2": False,
    "transcode_video": False,
    "transcode_audio": False,
    "corrupt_fix": False,
    "replace_files": False,
    "threads": DEFAULT_THREADS,
    "par_mode": "auto",
    "par_value": "1:1",
    "dar_mode": "auto",
    "dar_value": "16:9"
}

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
        "name": "Archive (ProRes/PCM)",
        "video_codec": "prores_ks",
        "audio_codec": "pcm_s32le",
        "crf": 10,
        "abr": 512,
        "preset": "Medium",
        "output_format": "MOV"
    }
}