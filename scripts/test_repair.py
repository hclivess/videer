"""
Checks Check & Repair against files damaged on purpose, through the real worker and a real FFmpeg (#20).

Run from the repository root:  python scripts/test_repair.py
"""
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.repair_manager import (RepairWorker, build_reencode_command, build_remux_command, edit_list_hides_video,
                                    expected_frames, missing_frames, picture_shortfall)
from utils.ffmpeg_utils import probe_media_info
from utils.file_utils import backup_path

failures = []
SETTINGS = {'video_codec': 'libx264', 'crf': 28, 'audio_codec': 'aac', 'audio_bitrate': '64k',
            'speed_preset': 'ultrafast', 'output_format': 'mp4'}


def check(name, condition, detail=""):
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


def make_source(path, duration=60):
    subprocess.run(['ffmpeg', '-v', 'error', '-y',
                    '-f', 'lavfi', '-i', f'testsrc2=s=320x240:r=30:d={duration}',
                    '-f', 'lavfi', '-i', f'sine=d={duration}',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '60', '-c:a', 'aac', '-b:a', '64k', path],
                   check=True)


def damage_video(source, target, start, end, junk=False, every=1):
    """Overwrite the video samples in [start, end). The index stays good and the audio is left alone."""
    packets = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                              '-show_entries', 'packet=pts_time,pos,size', '-of', 'csv=p=0', source],
                             capture_output=True, text=True, check=True).stdout
    data = bytearray(open(source, 'rb').read())
    rng = random.Random(1)
    for index, line in enumerate(packets.splitlines()):
        pts, size, pos = line.split(',')[:3]
        if start <= float(pts) < end and index % every == 0:
            for offset in range(int(pos), int(pos) + int(size)):
                data[offset] = rng.randrange(256) if junk else 0
    with open(target, 'wb') as handle:
        handle.write(data)


def shorten_edit_list(source, target, seconds):
    """Make the first edit list play only `seconds`, as a broken muxer does. Samples and tables are untouched."""
    data = bytearray(open(source, 'rb').read())
    at = data.find(b'mvhd')
    version = data[at + 4]
    timescale = struct.unpack('>I', data[at + 8 + (8 if version == 0 else 16):][:4])[0]
    entry = data.find(b'elst') + 12
    if data[entry - 8] != 0:
        raise ValueError("expected a version 0 edit list")
    data[entry:entry + 4] = struct.pack('>I', int(seconds * timescale))
    with open(target, 'wb') as handle:
        handle.write(data)


def video_md5(path):
    return subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-map', '0:v', '-f', 'md5', '-'],
                          capture_output=True, text=True, check=True).stdout.strip()


def repair(folder, source, name, strategy='auto', replace=False, **overrides):
    """Repair a private copy of the source, returning the report and the folder's listing"""
    work = os.path.join(folder, name)
    os.makedirs(work)
    path = os.path.join(work, 'test.mp4')
    shutil.copy(source, path)
    worker = RepairWorker([path], {**SETTINGS, **overrides}, strategy, replace_originals=replace)
    worker.run()
    return worker.reports[0], sorted(os.listdir(work))


with tempfile.TemporaryDirectory() as folder:
    good = os.path.join(folder, 'good.mp4')
    tail = os.path.join(folder, 'tail.mp4')
    hole = os.path.join(folder, 'hole.mp4')
    make_source(good)
    damage_video(good, tail, 17, 999)
    damage_video(good, hole, 20, 40)

    print("the winner survives when both strategies land on one name")
    report, listing = repair(folder, tail, 'same_name')
    check("the output named in the report exists", report['output'] and os.path.isfile(report['output']), report)
    check("it has the promised name", listing == ['test.mp4', 'test.repaired.mp4'], listing)
    check("the report gives its size", report['size'], report)

    print("video that stops early is not 'repaired'")
    check("mp4 output: partial", report['status'] == 'partial', report['status'])
    check("counts the frames that came through", 480 <= report['frames_after'] <= 540, report['frames_after'])
    check("says so in frames", "of 1800 video frames" in report['note'], report['note'])
    report, _ = repair(folder, tail, 'mkv', output_format='mkv')
    check("mkv output: partial", report['status'] == 'partial', report['status'])
    check("container duration still looks complete, which is the trap",
          abs(report['duration_after'] - 60) < 1, report['duration_after'])

    print("a hole filled with repeats is still a hole")
    report, _ = repair(folder, hole, 'hole')
    check("mp4 holds the full count, yet: partial", report['status'] == 'partial', report)
    check("the repeats are not counted", report['frames_after'] < 1300, report['frames_after'])
    report, _ = repair(folder, hole, 'hole_mkv', output_format='mkv')
    check("mkv leaves a real gap: partial", report['status'] == 'partial', report)

    print("an index that only promises what survived is not believed")
    short = os.path.join(folder, 'short.mp4')
    short_damaged = os.path.join(folder, 'short_damaged.mp4')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-t', '17', '-i', good, '-i', good,
                    '-map', '0:v', '-map', '1:a', '-c', 'copy', short], check=True)
    damage_video(short, short_damaged, 16, 16.1)
    for container in ('mp4', 'mkv'):
        report, _ = repair(folder, short_damaged, f'short_{container}', output_format=container)
        check(f"{container}: nothing missing by the index, yet partial", report['status'] == 'partial', report)
        check(f"{container}: says where the picture stops", "picture stops at" in report['note'], report['note'])
    report, listing = repair(folder, short_damaged, 'short_replace', replace=True)
    check("and does not replace the original", "not replaced" in report['note'] and len(listing) == 2, listing)
    report, _ = repair(folder, short, 'short_clean')
    check("the same shape undamaged is left alone", report['status'] == 'clean', report)

    print("a wrong edit list hides video that is all there")
    cut_list = os.path.join(folder, 'cut_list.mp4')
    shorten_edit_list(good, cut_list, 17)
    info = probe_media_info(cut_list)
    check("the probe shows the signature", info['frames'] == 1800 and abs(info['video_duration'] - 17) < 0.1, info)
    check("it is recognised", edit_list_hides_video(info))
    worker = RepairWorker([cut_list], SETTINGS, 'auto', check_only=True)
    worker.run()
    check("check only: damaged, though nothing errors", worker.reports[0]['status'] == 'damaged', worker.reports[0])
    check("check only: says what it is", "edit list" in worker.reports[0]['note'], worker.reports[0]['note'])
    report, listing = repair(folder, cut_list, 'cut_list')
    check("repaired", report['status'] == 'repaired', report)
    check("every frame back", report['frames_after'] == 1800, report['frames_after'])
    check("without re-encoding", report['strategy'] == 'remux', report['strategy'])
    check("the note explains it", "edit list" in report['note'], report['note'])
    check("the video is bit-identical to before the damage", video_md5(report['output']) == video_md5(good))
    report, _ = repair(folder, cut_list, 'cut_list_reencode', strategy='reencode')
    check("a re-encode gets every frame too", report['frames_after'] == 1800 and report['status'] == 'repaired', report)

    print("an edit list is only overruled on that evidence")
    check("a short video with long audio", not edit_list_hides_video(probe_media_info(short)))
    check("the undamaged source", not edit_list_hides_video(probe_media_info(good)))
    trim = os.path.join(folder, 'trim.mp4')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', '3.3', '-i', good, '-t', '10', '-c', 'copy', trim], check=True)
    check("a stream-copied cut", not edit_list_hides_video(probe_media_info(trim)))
    check("not given to a demuxer without the option",
          not edit_list_hides_video({'format_name': 'matroska,webm', 'frames': 1800, 'fps': 30,
                                     'duration': 60, 'video_duration': 17}))
    for name, command in (('remux', build_remux_command('ffmpeg', 'in.mp4', 'out.mp4', ['-x', '0'])),
                          ('re-encode', build_reencode_command(SETTINGS, 'in.mp4', 'out.mp4', ['-x', '0']))):
        check(f"{name}: the option goes on the input", command[command.index('-i') - 2:command.index('-i')] == ['-x', '0'],
              command)

    print("failure is reported, and does not cost the attempt that worked")
    report, listing = repair(folder, tail, 'failed_reencode', ffmpeg_extras='-no-such-option')
    check("the remux is kept", report['strategy'] == 'remux' and 'test.repaired.mp4' in listing, (report, listing))
    check("the report says why the re-encode failed", "no-such-option" in report['note'], report['note'])
    report, listing = repair(folder, tail, 'failed', strategy='reencode', ffmpeg_extras='-no-such-option')
    check("nothing usable: failed", report['status'] == 'failed', report['status'])
    check("with FFmpeg's reason", "no-such-option" in report['note'], report['note'])
    check("and no output claimed", report['output'] is None, report['output'])
    check("no attempt left behind", listing == ['test.mp4'], listing)

    print("replacing the original")
    report, listing = repair(folder, tail, 'replace_partial', replace=True)
    check("a partial recovery does not replace it", listing == ['test.mp4', 'test.repaired.mp4'], listing)
    check("and says so", "not replaced" in report['note'], report['note'])

    print("complete repairs are still repairs")
    junk = os.path.join(folder, 'junk.mp4')
    damage_video(good, junk, 20, 22, junk=True, every=7)
    report, listing = repair(folder, junk, 'concealed')
    check("every frame there: repaired", report['status'] == 'repaired', report)
    check("and warned the picture was concealed", "concealed" in report['note'], report['note'])
    report, listing = repair(folder, junk, 'replace_good', replace=True)
    check("a complete repair does replace", (report['output'].endswith('test.mp4')
                                         and os.path.basename(backup_path('test.mp4')) in listing),
          (report['output'], listing))
    report, listing = repair(folder, good, 'clean')
    check("an undamaged file is clean", report['status'] == 'clean', report)
    check("and nothing is written", listing == ['test.mp4'], listing)

    print("expected frames do not flag healthy files")
    cut = os.path.join(folder, 'cut.mp4')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', '3.3', '-i', good, '-t', '10', '-c', 'copy', cut], check=True)
    info = probe_media_info(cut)
    expected, stated = expected_frames(info)
    check("pre-roll samples do not count", expected == 300 and stated, (expected, info['frames']))
    tagged = os.path.join(folder, 'tagged.mkv')
    subprocess.run(['ffmpeg', '-v', 'error', '-y',
                    '-f', 'lavfi', '-i', 'testsrc2=s=320x240:r=30:d=10', '-f', 'lavfi', '-i', 'sine=d=30',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', tagged], check=True)
    check("longer audio does not inflate a Matroska video", expected_frames(probe_media_info(tagged)) == (300, True),
          expected_frames(probe_media_info(tagged)))

print("jitter is not loss")
check("a few frames short", missing_frames(5400, 5380, 30) == 0)
check("minutes short", missing_frames(5400, 1500, 30) == 3900)
check("an estimate gets more room", missing_frames(5400, 5200, 30, stated=False) == 0)
check("no expectation, no verdict", missing_frames(None, 10, 30) == 0)
check("audio a moment past the picture", picture_shortfall(178.5, 180) == 0)
check("minutes of no picture", picture_shortfall(50, 527) == 477)
check("no stream duration, no verdict", picture_shortfall(None, 527) == 0)

if failures:
    print(f"\n{len(failures)} failed: {', '.join(failures)}")
    sys.exit(1)
print("\nall passed")
