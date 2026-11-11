"""
Checks the fixes from the 3.17.6 review: nothing the user owns is overwritten or lost, per-file settings
survive and reach the whole pipeline, and the user's own FFmpeg options are passed on as typed.

Run from the repository root:  python scripts/test_queue_safety.py
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import config
from models.file_models import VideoFile
from modules.frameservers.base import Frameserver
from modules.process_manager import ProcessThread
from modules.queue_manager import read_queue_file
from modules.repair_manager import RepairWorker
from utils import childproc
from utils.ffmpeg_utils import FFmpegCommandBuilder, find_ffmpeg, find_ffprobe, split_extras
from utils.file_utils import FileOperations, backup_path

failures = []
work = tempfile.mkdtemp(prefix="videer-test-")


def check(name, condition, detail=""):
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


def write(name, text="x"):
    path = os.path.join(work, name)
    with open(path, "w") as fh:
        fh.write(text)
    return path


def read(path):
    with open(path) as fh:
        return fh.read()


print("replacing an original")
ops = FileOperations()
original, encode = write("tape.mkv", "original"), write("tape_x264.mkv", "first encode")
ops.replace_file_as(encode, original)
second = write("tape_x264.mkv", "second encode")
ops.replace_file_as(second, original)
check("a second replace keeps the first backup", read(backup_path(original)) == "original",
      os.listdir(work))
check("and backs up what it replaced beside it", read(original + ".old2.mkv") == "first encode", os.listdir(work))

original, encode = write("keep.mkv", "original"), write("keep_x264.mkv", "encode")
real_move = __import__("shutil").move
import utils.file_utils as file_utils
file_utils.shutil.move = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
try:
    result = ops.replace_file_as(encode, original)
finally:
    file_utils.shutil.move = real_move
check("a failed move reports failure", result is None)
check("and puts the original back under its own name", os.path.exists(original) and read(original) == "original"
      and not os.path.exists(backup_path(original)), os.listdir(work))

print("queue files")
queue = write("queue.json", json.dumps({"files": [{"path": "/a.mkv", "status": "success",
                                                    "overrides": {"crf": 30}}, "/b.mkv"]}))
entries = read_queue_file(queue)["files"]
check("per-file overrides survive loading", entries[0].get("overrides") == {"crf": 30}, entries)
check("a bare path still loads", entries[1] == {"path": "/b.mkv", "status": "pending"}, entries)

print("the queue run")


class FakeManager:
    def __init__(self, files):
        self.files = files

    def get_file_at(self, index):
        return self.files[index] if index < len(self.files) else None

    def get_total_file_count(self):
        return len(self.files)

    def set_worker_index(self, index):
        pass


settings = dict(config.DEFAULT_SETTINGS)
a, b = VideoFile(write("clip.avi")), VideoFile(write("clip.mp4"))
thread = ProcessThread(FakeManager([a, b]), settings)
for file in (a, b):
    file.set_output_name(settings)
    thread._claim_output(file)
check("same-named sources get different outputs", a.output_name != b.output_name and ".mp4" in b.output_name,
      (a.output_name, b.output_name))

done, todo = VideoFile(write("done.mkv")), VideoFile(write("todo.mkv"))
done.status = "success"
todo.set_overrides({"deinterlace": True, "tff": True})
settings = dict(config.DEFAULT_SETTINGS, deinterlace=False, deinterlacer="qtempgaussmc", use_avisynth=False)
thread = ProcessThread(FakeManager([done, todo]), settings, skip_completed=True)
seen = []
thread._process_file = lambda file: seen.append((file.filename, thread.settings.get("deinterlace"),
                                                 thread.settings.get("tff"),
                                                 type(thread.frameserver).__name__)) or False
thread._run_queue()
check("a file that already succeeded is skipped", [s[0] for s in seen] == ["todo.mkv"], seen)
check("a file's own deinterlace setting reaches the frameserver",
      seen and seen[0][1:] == (True, True, "VapourSynthFrameserver"), seen)
check("the queue's own settings are untouched", thread.queue_settings.get("deinterlace") is False)
for file in (done, todo):
    file.close_logger()

print("files beside the source that are not videer's")
video = VideoFile(write("movie.mkv"))
server = Frameserver({})
server.script_extension = ".vpy"
check("a free script name is used as is", server.script_path(video).endswith("movie.vpy"))
write("movie.vpy", "the user's own")
check("an existing script is not overwritten", server.script_path(video).endswith("movie.videer.vpy"))
write("movie.mkv.ffindex")
video.set_output_name(dict(config.DEFAULT_SETTINGS))
video.cleanup_temp_files()
check("an existing index is not deleted", os.path.exists(video.filepath + ".ffindex"))

print("extra FFmpeg parameters")
typed = r'-vf "scale=1280:-2" -metadata title="My Movie" -i C:\clips\a.mkv'
check("quotes group and are removed on Windows", split_extras(typed, windows=True) ==
      ['-vf', 'scale=1280:-2', '-metadata', 'title=My Movie', '-i', r'C:\clips\a.mkv'], split_extras(typed, windows=True))
check("and elsewhere", split_extras('-metadata title="My Movie"', windows=False) == ['-metadata', 'title=My Movie'])
check("an unclosed quote does not raise", split_extras('-metadata title="My', windows=False) == ['-metadata', 'title="My'])

settings = dict(config.DEFAULT_SETTINGS, video_codec="libsvtav1", grain_mode=True,
                ffmpeg_extras="-pix_fmt yuv420p10le -svtav1-params tune=0")
command = FFmpegCommandBuilder(settings).build_main_command("in.mkv", "out.mkv")
formats = [command[i + 1] for i, arg in enumerate(command) if arg == "-pix_fmt"]
params = [command[i + 1] for i, arg in enumerate(command) if arg == "-svtav1-params"]
check("the user's -pix_fmt is the last one", formats[-1] == "yuv420p10le", formats)
check("the user's encoder params join the grain settings", len(params) == 1 and "film-grain" in params[0]
      and params[0].endswith("tune=0"), params)

print("deinterlacing and the quality score")
settings = dict(config.DEFAULT_SETTINGS, deinterlace=True, deinterlacer="bwdif", use_avisynth=True, tff=True)
check("bwdif still runs when AviSynth+ is on", any(f.startswith("bwdif") for f in
      FFmpegCommandBuilder(settings).build_video_filters()))
settings = dict(config.DEFAULT_SETTINGS, deinterlace=True, deinterlacer="qtgmc", use_avisynth=True, tff=True)
builder = FFmpegCommandBuilder(settings)
check("QTGMC is not doubled by an FFmpeg filter", not any("bwdif" in f for f in builder.build_video_filters()))
graph = builder.build_metric_command("enc.mkv", "src.mkv", "ssim")
graph = graph[graph.index("-lavfi") + 1]
check("the reference is deinterlaced to the same frames", "[1:v]bwdif=mode=send_field:parity=tff," in graph, graph)

print("probe output is read as UTF-8")
result = childproc.run([sys.executable, "-c",
                        "import sys; sys.stdout.buffer.write(b'\\xd0\\x9f\\xd1\\x81 \\xff')"],
                       text=True, env=dict(os.environ, LC_ALL="C", PYTHONUTF8="0"))
check("whatever the locale, and without raising on a bad byte", result.stdout.startswith("Пс "), repr(result.stdout))

print("Check & Repair")
worker = RepairWorker.__new__(RepairWorker)
worker.should_stop = False
worker._run = lambda command, on_line: (on_line("progress=continue"), -9)[1]
state = worker.check("ffmpeg", "x.mkv")
check("a check killed partway is not a clean file", state["incomplete"] and not state["cancelled"], state)
worker._run = lambda command, on_line: (on_line("progress=continue"), setattr(worker, "should_stop", True), 1)[2]
state = worker.check("ffmpeg", "x.mkv")
check("a cancelled check says so", state["cancelled"] and not state["incomplete"], state)
worker.should_stop = False
worker._run = lambda command, on_line: (on_line("progress=end"), on_line("[h264] error while decoding"), 69)[2]
state = worker.check("ffmpeg", "x.mkv")
check("a damaged file that was read to the end is still just damaged", not state["incomplete"] and state["errors"] == 1, state)

ffmpeg, ffprobe = find_ffmpeg(), find_ffprobe()
if ffmpeg and ffprobe:
    print("cover art")
    cover, source, output = (os.path.join(work, n) for n in ("cover.png", "art.mp4", "art_out.mkv"))
    quiet = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i", "color=red:s=64x64", "-frames:v", "1", cover], **quiet)
    subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc=s=128x96:d=1", "-i", cover, "-map", "0", "-map", "1",
                    "-c:v:0", "mpeg4", "-c:v:1", "png", "-disposition:v:1", "attached_pic", source], **quiet)
    settings = dict(config.DEFAULT_SETTINGS, video_codec="mpeg4", audio_codec="copy", output_format="mkv")
    command = FFmpegCommandBuilder(settings).build_main_command(source, output)
    command[command.index("mpeg4")] = "mpeg4"
    ran = subprocess.run(command, **quiet)
    streams = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v", "-show_entries", "stream=index",
                              "-of", "csv=p=0", output], capture_output=True, text=True).stdout.split()
    check("the encode has one video track, not the cover as a second", ran.returncode == 0 and len(streams) == 1,
          (ran.returncode, streams))
else:
    print("cover art: skipped, no FFmpeg")

print()
print("FAILED: " + ", ".join(failures) if failures else "all passed")
sys.exit(1 if failures else 0)
