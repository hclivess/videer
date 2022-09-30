# videer
FFmpeg batch GUI with AviSynth support for **deinterlacing** and profile configuration that can be used in **frameserving** ([link](https://github.com/satishsampath/frame-server)). Created as a replacement for inflexible batch files that do not allow multiple encodings at the same time easily.

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
