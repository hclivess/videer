"""
Configuration file for videer
Contains all constants and default settings
"""

import os
import sys
import multiprocessing

# Application info
APP_NAME = "videer"
APP_VERSION = "3.13"
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

# ------------------------------------------------------------------
# Quality matching: find the CRF that keeps the source's quality
# ------------------------------------------------------------------
# The search is driven by a full-reference metric — the encode is compared against the source itself, so the
# answer is "how much of *this* file survived", not a bitrate guess that ignores what the content is.
#
# No metric is the truth. VMAF is the one trained against human scores, but it was trained for 1080p viewed
# from three screen-heights away, so it reads high on 4K and on sources far from that setup, and it can be
# talked up by anything that adds apparent sharpness. The alternatives disagree in useful ways: NEG removes
# the enhancement gain, the 4K model re-anchors the scale for UHD, MS-SSIM and SSIM are structural rather
# than trained, and PSNR/XPSNR are pure signal error. Pick the one whose bias you understand.
#
# 'family' says how a score is read back: 'libvmaf' writes a CSV log with one row per frame and the value in
# the named column; 'stats' writes a per-frame stats file that 'frame_re' pulls the value out of. Both also
# print a summary line, matched by 'summary_re', which is the fallback when the log cannot be read.
QUALITY_METRICS = {
    "vmaf": {
        "label": "VMAF",
        "filter": "libvmaf",
        "family": "libvmaf",
        # Deliberately no model argument: this *is* libvmaf's default model, and naming it would break the
        # older builds whose filter has no `model` option at all. The variants below need that option and
        # are opt-in, so they can require it.
        "args": {},
        "column": "vmaf",
        "summary_re": r"VMAF score\s*[:=]\s*([\d.]+)",
        "targets": [("Visually lossless — 97", 97.0), ("Transparent — 95 (recommended)", 95.0),
                    ("High — 93", 93.0), ("Good — 90", 90.0)],
        "default_target": 95.0,
        "range": (30.0, 100.0),
        "decimals": 1,
        "step": 0.5,
        "unit": "",
        "note": "Netflix's trained metric, on a 0-100 scale anchored to 1080p viewing. The default choice, "
                "and the one whose numbers other people's numbers can be compared with.",
    },
    "vmaf_neg": {
        "label": "VMAF NEG",
        "filter": "libvmaf",
        "family": "libvmaf",
        "args": {"model": "version=vmaf_v0.6.1neg"},
        "column": "vmaf",
        "summary_re": r"VMAF score\s*[:=]\s*([\d.]+)",
        "targets": [("Visually lossless — 96", 96.0), ("Transparent — 93 (recommended)", 93.0),
                    ("High — 91", 91.0), ("Good — 88", 88.0)],
        "default_target": 93.0,
        "range": (30.0, 100.0),
        "decimals": 1,
        "step": 0.5,
        "unit": "",
        "note": "VMAF with the enhancement gain removed: sharpening and contrast tricks that flatter plain "
                "VMAF earn nothing here. Scores a point or two lower than VMAF for the same encode, and is "
                "the stricter answer when plain VMAF looks too generous.",
    },
    "vmaf_4k": {
        "label": "VMAF 4K",
        "filter": "libvmaf",
        "family": "libvmaf",
        "args": {"model": "version=vmaf_4k_v0.6.1"},
        "column": "vmaf",
        "summary_re": r"VMAF score\s*[:=]\s*([\d.]+)",
        "targets": [("Visually lossless — 97", 97.0), ("Transparent — 95 (recommended)", 95.0),
                    ("High — 93", 93.0), ("Good — 90", 90.0)],
        "default_target": 95.0,
        "range": (30.0, 100.0),
        "decimals": 1,
        "step": 0.5,
        "unit": "",
        "note": "The VMAF model trained for 4K displays at 1.5 screen-heights. Use it for UHD sources; on "
                "smaller ones it reads several points higher than it should.",
    },
    "ms_ssim": {
        "label": "MS-SSIM",
        "filter": "libvmaf",
        "family": "libvmaf",
        "args": {"model": "version=vmaf_v0.6.1", "feature": "name=float_ms_ssim"},
        "column": "float_ms_ssim",
        "summary_re": None,
        "targets": [("Visually lossless — 0.995", 0.995), ("Transparent — 0.990 (recommended)", 0.990),
                    ("High — 0.985", 0.985), ("Good — 0.980", 0.980)],
        "default_target": 0.990,
        "range": (0.500, 1.000),
        "decimals": 4,
        "step": 0.001,
        "unit": "",
        "note": "Multi-scale structural similarity: measured, not trained, and judged at several scales at "
                "once. Tracks perceived quality better than plain SSIM and cannot be gamed by sharpening.",
    },
    "ssim": {
        "label": "SSIM",
        "filter": "ssim",
        "family": "stats",
        "args": {},
        "frame_re": r"\bAll:\s*([\d.]+)",
        "summary_re": r"SSIM\b.*?\bAll\s*[:=]\s*([\d.]+)",
        "targets": [("Visually lossless — 0.990", 0.990), ("Transparent — 0.985", 0.985),
                    ("High — 0.980 (recommended)", 0.980), ("Good — 0.970", 0.970)],
        "default_target": 0.980,
        "range": (0.500, 1.000),
        "decimals": 4,
        "step": 0.001,
        "unit": "",
        "note": "Structural similarity on the luma and chroma planes. Always available — it needs no libvmaf "
                "— but grain drags it down hard, so its targets mean less on noisy sources.",
    },
    "psnr": {
        "label": "PSNR",
        "filter": "psnr",
        "family": "stats",
        "args": {},
        "frame_re": r"psnr_avg:\s*([\d.]+|inf)",
        "summary_re": r"PSNR\b.*?\baverage\s*[:=]\s*([\d.]+)",
        "targets": [("Visually lossless — 45 dB", 45.0), ("Transparent — 42 dB (recommended)", 42.0),
                    ("High — 40 dB", 40.0), ("Good — 38 dB", 38.0)],
        "default_target": 42.0,
        "range": (20.0, 70.0),
        "decimals": 2,
        "step": 0.5,
        "unit": " dB",
        "note": "Plain signal error in decibels. It knows nothing about what the eye notices, and the dB that "
                "counts as good moves with the content — useful as a familiar sanity check, not as a target.",
    },
    "xpsnr": {
        "label": "XPSNR",
        "filter": "xpsnr",
        "family": "stats",
        "args": {},
        "frame_re": r"XPSNR\s+y:\s*([\d.]+|inf)",
        "summary_re": r"XPSNR\s+y:\s*([\d.]+)",
        "targets": [("Visually lossless — 44 dB", 44.0), ("Transparent — 41 dB (recommended)", 41.0),
                    ("High — 39 dB", 39.0), ("Good — 37 dB", 37.0)],
        "default_target": 41.0,
        "range": (15.0, 70.0),
        "decimals": 2,
        "step": 0.5,
        "unit": " dB",
        "note": "PSNR weighted by how visible the error is in each block — the metric the JVET codec groups "
                "use alongside PSNR. Reads the luma plane. Lower numbers than PSNR for the same encode.",
    },
}
DEFAULT_QUALITY_METRIC = "vmaf"

# How the per-frame scores become one number. The mean is what everybody quotes and what hides a bad scene
# inside a good average: twenty seconds of smeared motion in a ten-minute file barely move it. The percentile
# and minimum pools ask the opposite question — how bad does it get — and are the honest choice when a search
# on the mean keeps recommending a CRF that looks worse than the score promised.
QUALITY_POOLS = [
    ("Mean — the average frame", "mean"),
    ("Harmonic mean — bad frames count more", "harmonic"),
    ("5th percentile — the worst 1 frame in 20", "p5"),
    ("1st percentile — the worst 1 frame in 100", "p1"),
    ("Minimum — the single worst frame", "min"),
]
DEFAULT_QUALITY_POOL = "mean"

# Sampling: short clips spread across the file rather than one encode of the whole thing. The margin keeps
# the search away from intros, logos and credits, which compress nothing like the actual content.
QUALITY_SAMPLE_COUNT = 3
QUALITY_SAMPLE_SECONDS = 10
QUALITY_SAMPLE_MARGIN = 0.05

# Where the CRF/CQ search starts per encoder. Deliberately narrower than each encoder's full scale: outside
# these bounds the answer is never "the best trade-off", and every extra step costs a probe encode.
QUALITY_SEARCH_RANGE = {
    "libx264": (14, 34),
    "libx265": (16, 36),
    "h264_nvenc": (14, 38),
    "hevc_nvenc": (16, 40),
    "libsvtav1": (18, 50),
    "libvpx-vp9": (18, 50),
}
DEFAULT_QUALITY_SEARCH_RANGE = (16, 36)

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
    # Quality matching and verification
    "calculate_vmaf": False,          # key kept for preset compatibility: score every finished encode
    "auto_match_quality": False,      # find each file's own CRF before encoding it
    "quality_metric": DEFAULT_QUALITY_METRIC,
    "quality_target": 95.0,
    "quality_pool": DEFAULT_QUALITY_POOL,
    "quality_samples": QUALITY_SAMPLE_COUNT,
    "quality_sample_seconds": QUALITY_SAMPLE_SECONDS,
    "quality_crf_low": 0,             # 0 = the encoder's own default search range
    "quality_crf_high": 0,
    # NVENC quality options — FFmpeg's own defaults for these are all "off"
    "nvenc_aq": True,
    "nvenc_aq_strength": 8,
    "nvenc_lookahead": 32,
    "nvenc_multipass": "fullres",
    "nvenc_bframes": 3,
    "nvenc_b_ref": True
}

# Directory the app keeps its user-writable state in. In a PyInstaller build __file__ points inside the
# temporary extraction directory, which is wiped on exit — anything written there would not survive a
# restart — so use the directory the executable itself lives in.
APP_DIR = (os.path.dirname(os.path.abspath(sys.executable))
           if getattr(sys, 'frozen', False)
           else os.path.dirname(os.path.abspath(__file__)))

# Path to user defaults file (in the app directory)
DEFAULTS_FILE = os.path.join(APP_DIR, "defaults.json")

# Queue autosave: rewritten whenever the queue changes, restored on the next start
QUEUE_AUTOSAVE_FILE = os.path.join(APP_DIR, "queue.json")
QUEUE_FILE_FORMAT = 1

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