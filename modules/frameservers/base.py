"""
What every frameserver gives the encoder: a script written next to the source, and a description of how
FFmpeg is to read the frames it serves.

There are two shapes of that. AviSynth+ is a library FFmpeg opens itself — the script *is* the input, and it
carries the audio with it. VapourSynth is served by `vspipe`, a separate process whose y4m output is piped
into FFmpeg; a pipe carries only video, so the audio, subtitles, chapters and tags come from the source file
as a second input. FrameserverInput describes either, so the command builder and the process runner never
have to know which frameserver they are talking to.
"""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from models.file_models import VideoFile


class FrameserverError(Exception):
    """The script could not be written, or the frameserver cannot run it"""


@dataclass(frozen=True)
class FrameserverInput:
    """How FFmpeg reads a frameserved file"""

    script: str
    """The script that was written for this file."""

    video_args: List[str]
    """FFmpeg input arguments for the served video, e.g. ['-i', 'x.avs'] or ['-f', 'yuv4mpegpipe', '-i', 'pipe:0']."""

    producer: Optional[List[str]] = None
    """A command whose stdout feeds FFmpeg's stdin, or None when FFmpeg opens the script itself."""

    companion: Optional[str] = None
    """
    The file audio, subtitles, chapters and tags are taken from, as FFmpeg's second input — or None when the
    served stream already carries everything that is kept.
    """

    video_offset: float = 0.0
    """Seconds the source's video starts after its first stream, restored so a companion's audio stays in sync."""


class Frameserver:
    """A script-based processing stage in front of FFmpeg"""

    name = "frameserver"
    script_extension = ""

    def __init__(self, settings: Dict[str, Any]):
        self.settings = settings

    def script_path(self, video_file: VideoFile) -> str:
        # Next to the source, never in the process CWD: same-named sources in different folders must not collide
        return os.path.join(video_file.directory, f"{video_file.basename}{self.script_extension}")

    def prepare(self, video_file: VideoFile, source: str) -> FrameserverInput:
        """Write the script for `source` (the file itself, or its raw intermediate) and say how to read it"""
        raise NotImplementedError

    def problems(self) -> List[str]:
        """What stops this frameserver from running with these settings on this machine; empty when nothing"""
        return []

    def child_env(self, env: Dict[str, str]) -> Dict[str, str]:
        """The environment FFmpeg and the producer run in"""
        return env
