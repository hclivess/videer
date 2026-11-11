"""
Checks the AviSynth+ path used off Windows (issue #21) against a stand-in FFmpeg, without AviSynth+ installed.

The stand-in answers the probe script the way AviSynth+ would — it reads the FunctionExists() lines and
reports the ones "installed" — so this covers videer's side: what it asks, how it reads the answer, what it
tells the user and what script it writes. It does not prove AviSynth+ itself accepts the scripts.

Run from the repository root:  python scripts/test_avisynth_system.py
"""
import os
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.file_models import VideoFile
from modules import frameservers
from modules.frameservers import avisynth

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


if avisynth.WINDOWS:
    print("skipped: this exercises the non-Windows path")
    sys.exit(0)

FAKE = r'''#!/usr/bin/env python3
import os, re, sys
mode = os.environ.get("FAKE_AVS", "ok")
if "-demuxers" in sys.argv:
    print("Demuxers:\n D. = Demuxing supported\n .E = Muxing supported\n ---")
    print(" D   avi             AVI (Audio Video Interleaved)")
    if mode != "nodemuxer":
        print(" D   avisynth        AviSynth script")
    sys.exit(0)
script = sys.argv[sys.argv.index("-i") + 1]
if mode == "nolib":
    sys.stderr.write("[avisynth @ 0x1] AviSynth library could not be loaded: libavisynth.so\n")
    sys.stderr.write("Error opening input file %s.\n" % script)
    sys.exit(1)
asked = re.findall(r'FunctionExists\("(\w+)"\)', open(script).read())
have = os.environ.get("FAKE_AVS_HAVE", "").split()
sys.stderr.write("[avisynth @ 0x1] Assert: videer-probe:[%s]\n(%s, line 99)\n" % ("".join(n + " " for n in asked if n in have), script))
sys.stderr.write("Error opening input file %s.\n" % script)
sys.exit(1)
'''

work = tempfile.mkdtemp(prefix="videer-test-")
fake = os.path.join(work, "ffmpeg")
with open(fake, "w") as fh:
    fh.write(FAKE)
os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
avisynth.find_ffmpeg = lambda *a, **k: fake
avisynth.has_audio_stream = lambda path: True

QTGMC_ALL = "MSuper mt_lutxy RemoveGrain nnedi3 yadifmod2 FFT3DFilter"
BASE = {"use_avisynth": True, "use_ffms2": False, "avisynth_extras": "Version()"}
QTGMC = dict(BASE, deinterlace=True, deinterlacer="qtgmc", use_ffms2=True, preset="Medium")


def issues(settings, mode="ok", have=""):
    os.environ["FAKE_AVS"], os.environ["FAKE_AVS_HAVE"] = mode, have
    return frameservers.frameserver_issues(settings)


def script(settings, have):
    os.environ["FAKE_AVS"], os.environ["FAKE_AVS_HAVE"] = "ok", have
    avisynth._install = None        # as pressing Start does: ask the install again
    source = os.path.join(work, 'clip "one".mkv')
    open(source, "w").close()
    video = VideoFile(source)
    served = frameservers.frameserver_for(settings).prepare(video, source)
    with open(served.script) as fh:
        return fh.read(), video


print("the report from #21: AviSynth+ installed, a trivial script, no QTGMC")
got = issues(BASE, have="FFVideoSource FFAudioSource")
check("no complaint when AviSynth+ and a source filter are there", got == [], got)
check("nothing says 'only available on Windows'", not any("Windows" in i for i in issues(BASE, "nolib")))
text, video = script(BASE, "FFVideoSource FFAudioSource")
check("no Windows DLL is loaded", "LoadPlugin" not in text and ".dll" not in text, text)
check("the QTGMC scripts are not imported for a script that does not use them", "Import(" not in text, text)
check("the source is read with FFMS2 though the checkbox is off", 'FFVideoSource(' in text and "AVISource" not in text, text)
check("a file name with a quote in it stays one string", 'FFVideoSource("""' + video.filepath + '""", track=-1)' in text, text)
check("the user's lines are in the script", "\nVersion()\n" in text, text)

print("what is missing is named")
got = issues(BASE, "nodemuxer")
check("FFmpeg without the avisynth demuxer", len(got) == 1 and "--enable-avisynth" in got[0], got)
got = issues(BASE, "nolib")
check("AviSynth+ not loadable, with FFmpeg's own words", len(got) == 1 and "libavisynth" in got[0]
      and "Error opening input" in got[0], got)
got = issues(BASE, have="")
check("no source filter", len(got) == 1 and "source filter" in got[0] and "BestSource" in got[0], got)
got = issues(QTGMC, have="FFVideoSource FFAudioSource MSuper mt_lutxy")
check("QTGMC's missing plugins by name", len(got) == 1 and "RgTools, nnedi3" in got[0]
      and "mvtools2" not in got[0] and "fft3dfilter" not in got[0], got)
got = issues(dict(QTGMC, preset="Very Slow"), have="FFVideoSource FFAudioSource MSuper mt_lutxy RemoveGrain nnedi3")
check("Very Slow also needs fft3dfilter", len(got) == 1 and got[0].count("fft3dfilter") == 1, got)
got = issues(dict(QTGMC, preset="Ultra Fast"), have="FFVideoSource FFAudioSource MSuper mt_lutxy RemoveGrain yadifmod2")
check("Ultra Fast needs yadifmod2, not nnedi3", got == [], got)
got = issues(dict(QTGMC, use_ffms2=False), have="FFVideoSource FFAudioSource " + QTGMC_ALL)
check("the FFMS2 checkbox is not demanded where AVISource does not exist", got == [], got)

print("QTGMC through the system's plugins")
text, video = script(QTGMC, "LWLibavVideoSource LWLibavAudioSource " + QTGMC_ALL)
check("the bundled .avsi scripts are imported", text.count("Import(") == 2 and "QTGMC.avsi" in text, text)
check("still no DLL", "LoadPlugin" not in text, text)
check("L-SMASH-Works when that is what is installed", "v = LWLibavVideoSource(" in text
      and "a = LWLibavAudioSource(" in text and "track=-1" not in text, text)
check("QTGMC is called", 'QTGMC(Preset="Medium"' in text, text)
check("its index is cleaned up afterwards", video.index_files == [video.filepath + ".lwi"], video.index_files)
open(video.filepath + ".lwi", "w").close()
_, video = script(QTGMC, "LWLibavVideoSource LWLibavAudioSource " + QTGMC_ALL)
check("an index that was already there is left alone", video.index_files == [], video.index_files)

print()
print("FAILED: " + ", ".join(failures) if failures else "all passed")
sys.exit(1 if failures else 0)
