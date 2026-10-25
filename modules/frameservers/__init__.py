"""
Frameservers: script-based processing stages in front of FFmpeg.

    frameserver = frameserver_for(settings)     # None when FFmpeg reads the source itself
    served = frameserver.prepare(video_file, source)

At most one runs per encode. QTempGaussMC selects VapourSynth; QTGMC or the AviSynth+ checkbox select
AviSynth+. Asking for both is reported by frameserver_issues() rather than silently resolved.
"""

from typing import Any, Dict, List, Optional

from config import FRAMESERVER_DEINTERLACERS
from modules.frameservers.base import Frameserver, FrameserverError, FrameserverInput
from modules.frameservers.avisynth import AviSynthFrameserver
from modules.frameservers.vapoursynth import VapourSynthFrameserver

__all__ = ['Frameserver', 'FrameserverError', 'FrameserverInput', 'AviSynthFrameserver',
           'VapourSynthFrameserver', 'frameserver_for', 'frameserver_deinterlacer', 'frameserver_issues']


def frameserver_deinterlacer(settings: Dict[str, Any]) -> Optional[str]:
    """Name of the frameserver the selected deinterlacer runs in, or None for FFmpeg filters / no deinterlacing"""
    if not settings.get('deinterlace'):
        return None
    return FRAMESERVER_DEINTERLACERS.get(settings.get('deinterlacer', 'qtgmc'))


def frameserver_for(settings: Dict[str, Any]) -> Optional[Frameserver]:
    """The frameserver this encode runs through, or None when FFmpeg reads the source directly"""
    if frameserver_deinterlacer(settings) == 'vapoursynth':
        return VapourSynthFrameserver(settings)
    if settings.get('use_avisynth'):
        return AviSynthFrameserver(settings)
    return None


def frameserver_issues(settings: Dict[str, Any]) -> List[str]:
    """Everything that stops the configured frameserver work from happening as asked"""
    issues: List[str] = []
    if frameserver_deinterlacer(settings) == 'avisynth':
        if not settings.get('use_avisynth'):
            issues.append("QTGMC deinterlacing requires AviSynth+ to be enabled.")
        elif not settings.get('use_ffms2'):
            issues.append("QTGMC deinterlacing needs the FFMS2 source filter for non-AVI inputs.")
    frameserver = frameserver_for(settings)
    if frameserver is not None:
        issues.extend(frameserver.problems())
    return issues
