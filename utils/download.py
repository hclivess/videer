"""
Fetching files from the internet for the optional components videer installs itself (FFmpeg, VapourSynth).
Free of Qt, so it can be tested and driven from anywhere.
"""

import hashlib
import os
import ssl
import sys
import urllib.request
from typing import Callable, Optional, Sequence

# Where distributions keep the certificate authorities they trust, as one file
SYSTEM_CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",                   # Debian, Ubuntu, Arch, Manjaro, Gentoo
    "/etc/pki/tls/certs/ca-bundle.crt",                     # Fedora, RHEL
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",    # Fedora, RHEL, CentOS
    "/etc/ssl/ca-bundle.pem",                               # openSUSE
    "/var/lib/ca-certificates/ca-bundle.pem",               # openSUSE
    "/etc/ssl/cert.pem",                                    # macOS, Alpine, the BSDs
)


def certificate_bundle(default_file: Optional[str] = None, default_dir: Optional[str] = None,
                       candidates: Sequence[str] = SYSTEM_CA_BUNDLES, platform: str = sys.platform,
                       env=None) -> Optional[str]:
    """
    The CA bundle to verify downloads against, or None when Python's own default will do.

    The packaged build carries the OpenSSL of the machine it was built on, which looks for certificates where
    that machine keeps them: /usr/lib/ssl on Ubuntu. Arch, Manjaro, Fedora and openSUSE have nothing there, so
    every download failed with CERTIFICATE_VERIFY_FAILED and neither VapourSynth nor FFmpeg could be installed
    from inside the app (issue #21). When the default place is empty, use the system's own bundle; when there
    is none of those either, the one from certifi, which the packaged build carries.
    """
    env = os.environ if env is None else env
    if platform == "win32":
        return None                                         # the Windows certificate store, read by ssl itself
    if os.path.isfile(env.get("SSL_CERT_FILE", "")) or os.path.isdir(env.get("SSL_CERT_DIR", "")):
        return None                                         # the user said where; OpenSSL reads these itself
    if default_file is None and default_dir is None:
        paths = ssl.get_default_verify_paths()
        default_file, default_dir = paths.cafile, paths.capath
    if default_file and os.path.isfile(default_file):
        return None
    try:
        if default_dir and os.listdir(default_dir):
            return None
    except OSError:
        pass
    for path in candidates:
        if os.path.isfile(path):
            return path
    try:
        import certifi
        return certifi.where()
    except ImportError:
        return None


def ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certificate_bundle())


def download(url: str, target: str,
             on_progress: Optional[Callable[[int, int], None]] = None,
             should_stop: Optional[Callable[[], bool]] = None) -> None:
    """Fetch a file, reporting bytes as they arrive. Raises on anything that goes wrong."""
    request = urllib.request.Request(url, headers={'User-Agent': 'videer'})
    with urllib.request.urlopen(request, timeout=60, context=ssl_context()) as response, \
            open(target, 'wb') as handle:
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
