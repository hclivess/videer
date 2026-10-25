"""
Fetching files from the internet for the optional components videer installs itself (FFmpeg, VapourSynth).
Free of Qt, so it can be tested and driven from anywhere.
"""

import hashlib
import urllib.request
from typing import Callable, Optional


def download(url: str, target: str,
             on_progress: Optional[Callable[[int, int], None]] = None,
             should_stop: Optional[Callable[[], bool]] = None) -> None:
    """Fetch a file, reporting bytes as they arrive. Raises on anything that goes wrong."""
    request = urllib.request.Request(url, headers={'User-Agent': 'videer'})
    with urllib.request.urlopen(request, timeout=60) as response, open(target, 'wb') as handle:
        total = int(response.headers.get('Content-Length') or 0)
        done = 0
        while True:
            if should_stop and should_stop():
                raise InterruptedError("cancelled")
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            done += len(chunk)
            if on_progress:
                on_progress(done, total)


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()
