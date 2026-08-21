# videer

FFmpeg batch GUI with AviSynth+ support for **deinterlacing** and profile configuration that can be used in
[**frameserving**](https://github.com/satishsampath/frame-server). Created as a replacement for inflexible batch files
that do not allow multiple encodings at the same time easily.
Videer integrates `QTGMC`, which provides much smoother results than
ffmpeg's `yadif`, [see for yourself](https://www.youtube.com/watch?v=jE47A57T5FA). `QTGMC` is the best deinterlacer in
existence. Including AI and DaVinci Studio.

![videer processing a queue](thumb.png)

## Changes in 3.8.1

- **Quitting kills everything we started**: every FFmpeg/ffprobe process goes through `utils/childproc.py` — killed on
  normal quit, on Stop (including its children), and by the kernel if videer dies for any reason (Windows Job object,
  Linux `PR_SET_PDEATHSIG`).
- Duplicate queue entries are rejected: adding a file or folder that is already queued (same normalised absolute path)
  is skipped and reported.
- Natural sort everywhere files are collected or listed (`img2 < img10`, `Episode 9 < Episode 10`), via the shared
  `utils/naturalsort.py`.

## Changes in 3.8

- **Never a partial file**: FFmpeg now writes to `<name>.part.<ext>` and the file is renamed to its final name only
  when the encode completed and is non-empty. Stop or a failed encode removes the `.part` file.
- Packaging switched to PyInstaller; CI runs a real encode on the frozen Linux build (`VIDEER_SELFTEST`).

## Why

FFmpeg is a superior command line video encoding tool. Videer serves as a GUI for it.

## Notice

If you are not using a frameserver and are just trying to transcode video with any feature that requires AviSynth+, and
are running into issues, you should try with ffms2 enabled.

## Features

### Queue

- Add files, add folders (recursive), or drag & drop files/folders onto the list; batches are sorted naturally
  (`Tape 1, Tape 2, Tape 10` — not `1, 10, 2`)
- **Duplicate detection**: a file already in the queue is skipped, even if it is added again under a different spelling
  (relative/absolute path, different letter case, symlink) or appears twice in the same drop. Skipped files are
  reported in the status line.
- **Live queue**: add or remove files while encoding is running — pending entries are appended/removed without
  restarting; files already processed or in progress cannot be removed
- Drag entries to reorder; each entry is coloured by state (▶ running, ✔ done, ✖ failed) and shows its VMAF score
  when enabled
- **Pause / Resume** the whole run (suspends FFmpeg and its children, ETAs are corrected for the pause) and **Stop**

### Encoding

- Video: H.264 (x264), H.265/HEVC (x265), AV1 (SVT-AV1), VP9, NVIDIA NVENC H.264/HEVC (`p1`–`p7` presets), ProRes,
  raw, or stream copy; CRF quality and per-encoder speed presets
- Audio: AAC, MP3, Opus, AC3, FLAC, PCM, or stream copy; bitrate control and optional stereo downmix
- Containers: MKV (the only one that carries `pgssub`), MP4, AVI, MOV, WebM
- Deinterlacer choice: `QTGMC` (AviSynth+, Windows) or FFmpeg's `bwdif` / `yadif` (any OS, any input); field order
  and optional frame-rate halving. Picking QTGMC switches AviSynth+ and the ffms2 source filter on automatically
- Optional resolution scaling (2160p → 360p presets or custom W×H, "never upscale" guard, lanczos / bicubic / spline /
  bilinear / neighbor scaler)
- PAR / DAR presets and custom values, with metadata-only or resampling handling
- AviSynth+ pipeline with `SetFilterMTMode` multithreading, ffms2 source filter, optional raw pre-transcode for
  problematic inputs, and a free-form script box; every plugin QTGMC needs across *all* its presets is bundled in
  `plugins/` (masktools2, mvtools2, nnedi3, RgTools, ffms2, yadifmod2 for *Ultra Fast*, FFT3DFilter + FFTW for *Very Slow*)
- CUDA GPU acceleration support; 64-bit implementation
- Extra FFmpeg arguments pass-through; save / load your own presets, plus bundled Web / High Quality / Archive / AV1
  presets; `defaults.json` support for your own startup defaults

### File handling

- Output is written next to the source as `<name>_<vcodec>_<acodec>_crf<N>_abr<N>[_<res>].<ext>`; timestamps are
  copied from the original
- **Replace Original Files**: the encode takes over the original filename; the original is kept as `<name>.old<ext>`
- **Delete Source Files After Processing**: permanently deletes each source right after *its* encode succeeds — one by
  one as the queue progresses, so disk space is freed while long batches are still running. The source is only removed
  when the output exists and is non-empty; files that fail or are stopped keep their source. Combined with *Replace
  Original Files* the `.old` backup is removed too. There is no recycle bin and no undo — the start dialog warns you
  when this is on.
- Per-file `<name>.log` with the full FFmpeg command line and output; temporary `.avs` / `.ffindex` / `.trans.avi`
  files are cleaned up

### Feedback

- Live progress panel: per-file and total ETA, fps, speed, bitrate, output size, position — parsed from FFmpeg's
  machine-readable `-progress` output
- Settings sanity check before starting (container/codec mismatches, ignored options, missing AviSynth plugins,
  destructive options)
- Opt-in VMAF score after encoding (requires `libvmaf` in FFmpeg)
- Cross-platform: runs on Windows, Linux and macOS (AviSynth+/QTGMC features are Windows-only); no shell or
  PowerShell calls

## Requirements

- [FFmpeg](https://ffmpeg.org/) in the system PATH or next to videer (a Windows build is attached to the
  [latest release](https://github.com/hclivess/videer/releases/latest) as `ffmpeg.7z`)
- For QTGMC deinterlacing / AviSynth+ processing (Windows): [AviSynth+](https://avs-plus.net/) — the tested MT
  installer is attached to the [latest release](https://github.com/hclivess/videer/releases/latest) as
  `AviSynthPlus-MT-r2772.exe`
- No need to install AviSynth plugins, all are bundled in `plugins/`
- Python 3.9+ — on Windows run `run.cmd` (installs `requirements.txt` via pip and starts the app), or manually
  `pip install -r requirements.txt && python main.py`. Prebuilt binaries for Windows / Linux / macOS are attached to
  each release.
