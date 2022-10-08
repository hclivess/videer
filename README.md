# videer
FFmpeg batch GUI with AviSynth support for **deinterlacing** and profile configuration that can be used in **frameserving** ([link](https://github.com/satishsampath/frame-server)). Created as a replacement for inflexible batch files that do not allow multiple encodings at the same time easily.
Videer integrates `QTGMC`, which provides much smoother results than ffmpeg's `yadif`, [see for yourself](https://www.youtube.com/watch?v=jE47A57T5FA). `QTGMC` is the best deinterlacer in existence.

## Why
When videer was first made, there were two types of video processing software: editors and encoders/muxers. The first had terrible encoding/muxing options and the latter had horrible editing tools. With help of frameserving, high quality encoding became possible for professional video editing output. Videer is a tool to primarily help facilitate communication between the frame server and the encoder. However, it grew to become a quick and simple batch file converter as well. Unlike with Handbrake, Videer only allows users to change those settings that do not interfere with compatibility, flexibility and reliability of the final video file.

## Notice
If you want to deinterlace AVC (x264) video, running Videer with `raw transcode first` and `ffms2` on is optimal.

## New features:
- Multithreading using `SetFilterMTMode`
- AviSynth+
- 64bit Implementation
- Matroska format as it is the only one to support `pgssub`

## Requirements:
- [FFmpeg](https://ffmpeg.org/) (in system path)
- To use AviSynth+ or deinterlacing, you will need [AviSynth+](https://avs-plus.net/) installed
- No need to install plugins, all are bundled
- `pip install -r requirements.txt`

### Preview:    
![thumb](thumb.png)
