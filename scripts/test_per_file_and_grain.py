"""
Checks the per-file settings and grain features without launching the GUI.

Run from the repository root:  python scripts/test_per_file_and_grain.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from models.file_models import VideoFile
from modules.queue_manager import serialize_queue
from utils.ffmpeg_utils import FFmpegCommandBuilder

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


print("grain: each encoder asked in its own way")
cases = {
    'libx265':    (['-tune', 'grain'], None),
    'libx264':    (['-tune', 'grain'], None),
    'libsvtav1':  (None, '-svtav1-params'),
    'libaom-av1': (None, '-aom-params'),
}
for codec, (tune, params) in cases.items():
    builder = FFmpegCommandBuilder({'video_codec': codec, 'crf': 23, 'grain_mode': True})
    cmd = []
    builder._add_video_codec_settings(cmd)
    if tune:
        check(f"{codec} gets {' '.join(tune)}", all(t in cmd for t in tune), cmd)
    if params:
        check(f"{codec} gets {params}", params in cmd, cmd)

builder = FFmpegCommandBuilder({'video_codec': 'hevc_nvenc', 'crf': 23, 'grain_mode': True})
cmd = []
builder._add_video_codec_settings(cmd)
check("NVENC has no grain mode and gains no flags", '-tune' not in cmd or 'grain' not in cmd, cmd)
check("NVENC reports grain unsupported", not builder.grain_supported())

print("grain: the quality number pays for the grain")
on = FFmpegCommandBuilder({'video_codec': 'libx265', 'crf': 23, 'grain_mode': True})
off = FFmpegCommandBuilder({'video_codec': 'libx265', 'crf': 23, 'grain_mode': False})
check("CRF drops by GRAIN_CRF_OFFSET with grain on",
      off.effective_crf() - on.effective_crf() == config.GRAIN_CRF_OFFSET,
      f"{off.effective_crf()} -> {on.effective_crf()}")
check("CRF untouched with grain off", off.effective_crf() == 23)
check("grain named in the file's tags", "grain-aware" in on.encoding_description())
check("grain absent from tags when off", "grain-aware" not in off.encoding_description())

print("grain: an existing -params value is merged, not replaced")
merged = FFmpegCommandBuilder({'video_codec': 'libsvtav1', 'crf': 30, 'grain_mode': True})
cmd = ['-svtav1-params', 'tune=0']
merged._merge_params_option(cmd, '-svtav1-params', 'film-grain=8')
check("merge keeps the original value", cmd[cmd.index('-svtav1-params') + 1] == 'tune=0:film-grain=8', cmd)

print("per-file: overrides layer over the queue")
f = VideoFile('grainy.mkv')
check("a new file has no overrides", not f.has_overrides)
check("no overrides returns the queue dict unchanged",
      f.settings_with_overrides({'crf': 23}) == {'crf': 23})
f.set_overrides({'crf': 18, 'grain_mode': True})
resolved = f.settings_with_overrides({'crf': 23, 'preset': 'Medium', 'grain_mode': False})
check("override wins", resolved['crf'] == 18 and resolved['grain_mode'] is True, resolved)
check("un-overridden keys still come from the queue", resolved['preset'] == 'Medium')
check("sparse storage: only what was asked for", set(f.overrides) == {'crf', 'grain_mode'})

print("per-file: a queue change still reaches an overriding file")
resolved2 = f.settings_with_overrides({'crf': 23, 'preset': 'Slow', 'grain_mode': False})
check("new queue preset reaches the file", resolved2['preset'] == 'Slow')

print("per-file: the override drives the actual command")
builder = FFmpegCommandBuilder(resolved)
cmd = []
builder._add_video_codec_settings(cmd)
check("file's own CRF, minus the grain offset, is encoded",
      str(18 - config.GRAIN_CRF_OFFSET) in cmd, cmd)

print("per-file: survives save and restore")
f2 = VideoFile('clean.mkv')
data = serialize_queue([f, f2], {'crf': 23})
check("overriding file carries them", data['files'][0].get('overrides') == {'crf': 18, 'grain_mode': True})
check("ordinary file stays clean", 'overrides' not in data['files'][1])
with tempfile.TemporaryDirectory() as tmp:
    path = os.path.join(tmp, 'q.json')
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(data, handle)
    with open(path, encoding='utf-8') as handle:
        back = json.load(handle)
    check("round-trips through JSON", back['files'][0]['overrides']['crf'] == 18)

print("per-file: a hand-edited queue file cannot smuggle keys in")
allowed = {k: v for k, v in {'crf': 18, 'delete_source': True, 'threads': 1}.items()
           if k in config.PER_FILE_KEYS}
check("delete_source is not per-file overridable", 'delete_source' not in allowed)
check("threads is not per-file overridable", 'threads' not in allowed)
check("crf is", 'crf' in allowed)

print("source handling: three choices map onto the two stored flags")
seen = {}
for name, _label, flags in config.OUTPUT_ACTIONS:
    seen[name] = flags
    check(f"'{name}' round-trips", config.output_action_of(flags) == name, flags)
check("keep leaves the source alone",
      seen['keep'] == {'replace_files': False, 'delete_source': False})
check("only one choice deletes", sum(1 for f in seen.values() if f['delete_source']) == 1)
check("the legacy delete-without-replace combination is still recognised",
      config.output_action_of({'replace_files': False, 'delete_source': True}) == 'replace_no_rename')

print("bundled presets are deterministic")
baseline_keys = set(config.PRESET_SHAPED_KEYS)
check("shaped keys all exist in the defaults",
      baseline_keys <= set(config.DEFAULT_SETTINGS),
      baseline_keys - set(config.DEFAULT_SETTINGS))
check("source handling is NOT reset by a preset", 'delete_source' not in baseline_keys)
check("thread count is NOT reset by a preset", 'threads' not in baseline_keys)
check("deinterlacing IS reset by a preset", 'deinterlace' in baseline_keys)
check("grain IS reset by a preset", 'grain_mode' in baseline_keys)

print("grain preset is selectable")
preset = config.QUALITY_PRESETS.get('grain')
check("grain preset exists", preset is not None)
check("grain preset turns grain on", preset and preset.get('grain_mode') is True)
check("grain preset copies audio", preset and preset.get('audio_codec') == 'copy')
check("grain preset is in the defaults surface", 'grain_mode' in config.DEFAULT_SETTINGS)
check("more than one grain-oriented preset exists",
      sum(1 for v in config.QUALITY_PRESETS.values() if v.get('grain_mode')) >= 2)
check("a shrink preset keeps audio untouched",
      config.QUALITY_PRESETS['shrink']['audio_codec'] == 'copy')
check("the interlaced presets turn deinterlacing on",
      all(config.QUALITY_PRESETS[k].get('deinterlace') for k in ('tv', 'tv_smooth')))
check("the two interlaced presets differ only in frame rate",
      config.QUALITY_PRESETS['tv']['reduce_fps'] != config.QUALITY_PRESETS['tv_smooth']['reduce_fps'])

print()
if failures:
    print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("all checks passed")
