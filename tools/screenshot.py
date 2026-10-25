#!/usr/bin/env python3
"""
Take thumb.png and demo.gif from a real run, headlessly:

    python tools/screenshot.py

Generates three interlaced sample tapes (invented: FFmpeg test patterns, nothing of anyone's), queues them,
selects QTempGaussMC, encodes them with the stock settings, and records the window the whole way: the GIF is
every stage as it happened, thumb.png the finished queue. Needs FFmpeg and VapourSynth (Tools ▸ VapourSynth,
or `python main.py --install-vapoursynth`). Runs stateless — the user's own settings and queue are untouched.
Look at both images before committing them.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    # The offscreen platform has no font database of its own on Windows: without this every glyph is a box
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
os.environ["VIDEER_NO_STATE"] = "1"

from PySide6.QtCore import QTimer                         # noqa: E402
from PySide6.QtWidgets import QApplication                # noqa: E402
from PIL import Image                                      # noqa: E402

from utils.ffmpeg_utils import find_ffmpeg                # noqa: E402

WINDOW = (1200, 900)
GIF_WIDTH = 960
FRAME_MS = 250

# name, lavfi source, seconds — invented tapes, each a different test pattern so the queue does not look cloned
SAMPLES = [
    ("Tape 01 - Summer 1996.mkv", "testsrc2=size=720x576:rate=50", 6),
    ("Tape 02 - Birthday.mkv", "smptehdbars=size=720x576:rate=50", 5),
    ("Tape 03 - School Play.mkv", "mandelbrot=size=720x576:rate=50", 6),
]


def make_samples(folder: str) -> list:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise SystemExit("FFmpeg not found")
    paths = []
    for name, source, seconds in SAMPLES:
        path = os.path.join(folder, name)
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"{source},interlace=scan=tff,setfield=tff", "-f", "lavfi", "-i", "sine=frequency=330",
                        "-t", str(seconds), "-c:v", "libx264", "-flags", "+ilme+ildct", "-c:a", "aac", path],
                       check=True)
        paths.append(path)
    return paths


def main():
    work = tempfile.mkdtemp(prefix="videer-demo-")
    samples = make_samples(work)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    import main as app_main
    win = app_main.MainWindow()
    win.resize(*WINDOW)
    win.show()
    ui = win.ui_manager
    frames = []

    def snap():
        frames.append(win.grab().toImage())

    recorder = QTimer()
    recorder.timeout.connect(snap)

    def hold(ms: int, then):
        QTimer.singleShot(ms, then)

    def step_queue():
        win.file_manager.add_files(samples)
        ui.tabs.setCurrentIndex(0)
        hold(1500, step_processing)

    def step_processing():
        ui.tabs.setCurrentIndex(2)                      # Processing
        ui.controls['deinterlace'].setChecked(True)
        ui._select_deinterlacer('qtempgaussmc')
        ui.controls['tff'].setChecked(True)
        hold(6000, step_start)                          # the VapourSynth check reports "ready"

    def step_start():
        settings = ui.get_current_settings()
        issues = win.process_manager.validate_settings(settings)
        if issues:
            raise SystemExit("settings check failed: " + "; ".join(issues))
        win.process_manager.start_processing(win.file_manager.get_queue(), settings)
        ui.set_processing_state(True)

    def finished(ok, total):
        ui.set_processing_state(False)
        print(f"run finished: {ok}/{total}", flush=True)
        if ok != total:
            raise SystemExit("the demo run did not encode every file")
        hold(1500, done)

    def done():
        recorder.stop()
        ui.tabs.setCurrentIndex(0)
        app.processEvents()
        win.grab().save(os.path.join(ROOT, "thumb.png"))
        write_gif(frames, os.path.join(ROOT, "demo.gif"))
        shutil.rmtree(work, ignore_errors=True)
        print("thumb.png and demo.gif written — now LOOK at them", flush=True)
        app.quit()

    win.process_manager.processing_finished.disconnect(win.on_processing_finished)
    win.process_manager.processing_finished.connect(finished)
    recorder.start(FRAME_MS)
    hold(800, step_queue)
    QTimer.singleShot(900_000, lambda: (print("timeout", flush=True), app.exit(1)))
    sys.exit(app.exec())


def write_gif(qimages, path: str):
    """Scale, quantise against one shared palette (no flicker between frames) and drop exact repeats"""
    images = []
    for qimage in qimages:
        buffer = qimage.convertToFormat(qimage.Format.Format_RGB888)
        image = Image.frombytes("RGB", (buffer.width(), buffer.height()), bytes(buffer.constBits()),
                                "raw", "RGB", buffer.bytesPerLine())
        height = round(image.height * GIF_WIDTH / image.width)
        images.append(image.resize((GIF_WIDTH, height), Image.LANCZOS))
    palette = images[-1].quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    frames, durations = [], []
    for image in images:
        frame = image.quantize(palette=palette, dither=Image.Dither.NONE)
        if frames and frame.tobytes() == frames[-1].tobytes():
            durations[-1] += FRAME_MS
            continue
        frames.append(frame)
        durations.append(FRAME_MS)
    durations[-1] = 3000                                # rest on the finished queue before looping
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True)
    print(f"demo.gif: {len(frames)} frames, {os.path.getsize(path) / 1048576:.1f} MB", flush=True)


if __name__ == "__main__":
    main()
