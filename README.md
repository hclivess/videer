# videer

FFmpeg batch GUI with AviSynth support for **deinterlacing** and profile configuration that can be used in [*
*frameserving**](https://github.com/satishsampath/frame-server). Created as a replacement for inflexible batch files
that do not allow multiple encodings at the same time easily.
Videer integrates `QTGMC`, which provides much smoother results than
ffmpeg's `yadif`, [see for yourself](https://www.youtube.com/watch?v=jE47A57T5FA). `QTGMC` is the best deinterlacer in
existence. Including AI and DaVinci Studio.

## Notice

If you are not using a frameserver and are just trying to transcode video like a normal person, you must always run with the "use ffms2" checkbox enabled.

## Why

FFmpeg is a superior command line video encoding tool. Videer serves as a GUI for it.

## New features:

- Multithreading using `SetFilterMTMode`
- AviSynth+
- 64bit Implementation
- Matroska format as it is the only one to support `pgssub`
- CUDA GPU acceleration support

## Requirements:

- [FFmpeg](https://ffmpeg.org/) (in system path)
- To use AviSynth+ or deinterlacing, you will need [AviSynth+](https://avs-plus.net/) installed
- No need to install plugins, all are bundled
- `pip install -r requirements.txt`

### Preview:

![thumb](thumb.png)
