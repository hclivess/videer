# videer

A professional FFmpeg batch GUI with AviSynth+ support for **deinterlacing**, **PAR/DAR correction**, and advanced profile configuration. Designed for [**frameserving**](https://github.com/satishsampath/frame-server) workflows and batch video processing with superior quality.

## 🎯 Key Features

- **QTGMC Deinterlacing** - The best deinterlacer in existence, [superior to yadif and even AI solutions](https://www.youtube.com/watch?v=jE47A57T5FA)
- **PAR/DAR Support** - Full pixel aspect ratio handling with resampling options
- **Batch Processing** - Queue multiple files with individual progress tracking
- **Hardware Acceleration** - NVIDIA NVENC support for H.264/H.265
- **Modular Architecture** - Clean, maintainable codebase split into logical modules
- **Preset System** - Save and load custom encoding profiles

## 🆕 What's New in v3.1

### Architecture Improvements
- **Fully Modularized** - Split into separate modules to prevent file size limits
- **Enhanced UI** - Tabbed interface with drag-and-drop support
- **Process Management** - Improved threading and progress tracking
- **File Operations** - Timestamp preservation and safe file replacement

### PAR/DAR Features
- **Pixel Aspect Ratio Handling**
  - Automatic detection from source files
  - Presets for PAL, NTSC, HDV, DVCPRO formats
  - Choice between metadata flags or actual pixel resampling
  - Convert non-square pixels to square for maximum compatibility

- **Display Aspect Ratio Control**
  - Standard presets (4:3, 16:9, 21:9, cinema formats)
  - Custom ratio input
  - Automatic letterboxing/pillarboxing calculations

### Technical Enhancements
- **64-bit Implementation** - Full 64-bit support
- **Multi-threading** - Optimized with `SetFilterMTMode`
- **FFMS2 Support** - Better source compatibility
- **Container Formats** - MKV, MP4, AVI, MOV, WebM support
- **Metadata Preservation** - Maintains timestamps and chapters

## 📋 Requirements

### Core Dependencies
- **Python 3.8+**
- **FFmpeg** (must be in system PATH or application directory)
- **PySide6** - `pip install PySide6>=6.5.0`
- **psutil** - `pip install psutil>=5.9.0`

### Optional Dependencies
- **AviSynth+** - Required for deinterlacing and advanced filtering
- **FFMS2** - Recommended for better source compatibility

### Installation

```bash
# Clone repository
git clone https://github.com/yourusername/video-processor-pro.git
cd video-processor-pro

# Install Python dependencies
pip install -r requirements.txt

# Run application
python main.py
```

## 🚀 Quick Start

1. **Add Files**: Drag and drop video files or use "Add Files"/"Add Folder"
2. **Configure Settings**:
   - **Video**: Choose codec (H.264/H.265/ProRes), quality (CRF)
   - **Audio**: Select codec and bitrate
   - **PAR/DAR**: Set aspect ratio handling mode
   - **Processing**: Enable deinterlacing if needed
3. **Start Processing**: Click "Start Processing" to begin

## 🎮 Usage Examples

### Fix Non-Square Pixels (DV/Broadcast Content)
1. Set PAR Mode to detected format (e.g., "NTSC 4:3")
2. Choose "Resample to Square Pixels" for maximum compatibility
3. Process to get properly displayed video

### Deinterlace with QTGMC
1. Enable "Use AviSynth+" in Processing tab
2. Check "Enable Deinterlacing"
3. Choose field order (Top Field First for most content)
4. Select quality preset (Slower = better quality)

### Hardware Accelerated Encoding
1. Select "NVIDIA H.264 (NVENC)" or "NVIDIA H.265 (NVENC)"
2. Adjust CQ value (similar to CRF)
3. Requires NVIDIA GPU with NVENC support

## 📁 Project Structure

```
video_processor_pro/
├── main.py                 # Application entry point
├── config.py              # Configuration constants
├── models/                # Data models
│   └── file_models.py     # VideoFile and FileQueue classes
├── modules/               # Core functionality
│   ├── ui_manager.py      # UI creation and management
│   ├── file_manager.py    # File queue operations
│   ├── process_manager.py # FFmpeg process execution
│   ├── preset_manager.py  # Preset handling
│   └── avisynth_handler.py # AviSynth script generation
├── utils/                 # Utility functions
│   ├── ffmpeg_utils.py    # FFmpeg command building
│   ├── file_utils.py      # File operations
│   └── par_dar_utils.py   # Aspect ratio calculations
└── plugins/              # AviSynth plugins (bundled)
```

## 🎯 PAR/DAR Handling Modes

### Metadata Only (Default)
- Fastest processing
- Sets display flags in container
- Relies on player support

### Resample to Square Pixels
- Converts non-square pixels to square
- Maximum compatibility
- Slightly slower processing
- Recommended for web upload

### Preserve Original
- Keeps source PAR unchanged
- For archival purposes

## 🔧 Advanced Features

### Custom AviSynth Scripts
Add custom AviSynth commands in the Processing tab for advanced filtering.

### FFmpeg Extras
Add additional FFmpeg parameters in the Advanced tab for fine control.

### Preset Management
- Save current settings as reusable presets
- Import/export presets for sharing
- Built-in presets for common use cases

## 📊 Codec Support

### Video Codecs
- **H.264** (x264) - Universal compatibility
- **H.265/HEVC** (x265) - Better compression
- **NVIDIA NVENC** - Hardware acceleration
- **ProRes** - Professional/archival
- **Raw/Uncompressed** - Maximum quality
- **Copy** - No re-encoding

### Audio Codecs
- **AAC** - Standard for MP4/streaming
- **MP3** - Legacy compatibility
- **Opus** - Modern, efficient
- **AC3** - Surround sound
- **FLAC** - Lossless compression
- **PCM** - Uncompressed
- **Copy** - No re-encoding

## ⚠️ Important Notes

- When using frameserving, transcoding may be required first
- For AviSynth features with non-AVI sources, enable FFMS2
- QTGMC requires significant CPU resources but provides superior results
- PAR resampling increases file size but ensures universal compatibility

## 🤝 Contributing

Contributions are welcome! The modular architecture makes it easy to:
- Add new codecs or formats
- Implement additional filters
- Enhance the UI
- Add new processing features

## 📜 License

This project is provided as-is for video processing purposes.

## 🙏 Acknowledgments

- **FFmpeg** - The backbone of video processing
- **AviSynth+** - Advanced video filtering
- **QTGMC** - Superior deinterlacing algorithm
- **PySide6** - Modern Qt bindings for Python

## 📧 Support

For issues or questions:
1. Check existing issues on GitHub
2. Ensure FFmpeg is properly installed
3. Verify AviSynth+ plugins are in place
4. Review error logs in the application directory

---

**Note**: This application is the successor to Videer, rebuilt with a modular architecture for better maintainability and enhanced features including comprehensive PAR/DAR support.
