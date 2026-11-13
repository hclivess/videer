"""
Checks which certificate authorities the in-app downloads are verified against (issue #21).

The packaged build's OpenSSL looks where its build machine keeps certificates. On Arch, Manjaro, Fedora and
openSUSE nothing is there, and installing VapourSynth or FFmpeg from inside the app failed with
CERTIFICATE_VERIFY_FAILED. utils.download falls back to the system's bundle; this covers the choice. The real
thing — the packaged build downloading on those distributions — is a step in .github/workflows/build.yml.

Run from the repository root:  python scripts/test_download_certificates.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.download import certificate_bundle, ssl_context

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


work = tempfile.TemporaryDirectory(prefix="videer-certs-")
root = work.name
bundle = os.path.join(root, "ca-bundle.pem")
open(bundle, "w").close()
second = os.path.join(root, "second.pem")
open(second, "w").close()
full_dir = os.path.join(root, "certs")
os.mkdir(full_dir)
open(os.path.join(full_dir, "a.0"), "w").close()
empty_dir = os.path.join(root, "empty")
os.mkdir(empty_dir)
gone = os.path.join(root, "usr-lib-ssl")


def pick(**kw):
    kw.setdefault("env", {})
    kw.setdefault("platform", "linux")
    kw.setdefault("candidates", (os.path.join(root, "nowhere.pem"), bundle, second))
    return certificate_bundle(**kw)


print("the default place works")
check("a default file that exists is left alone", pick(default_file=bundle, default_dir=gone) is None)
check("a default folder with certificates is left alone", pick(default_file=gone, default_dir=full_dir) is None)

print("the default place is empty: the build machine's path on another distribution")
got = pick(default_file=os.path.join(gone, "cert.pem"), default_dir=os.path.join(gone, "certs"))
check("the system's bundle is used", got == bundle, got)
got = pick(default_file=os.path.join(gone, "cert.pem"), default_dir=empty_dir)
check("an empty default folder counts as nothing", got == bundle, got)
got = pick(default_file=gone, default_dir=gone, candidates=(second, bundle))
check("the first bundle that exists wins", got == second, got)

print("no system bundle either")
got = pick(default_file=gone, default_dir=gone, candidates=())
try:
    import certifi
    check("certifi's bundle is used", got == certifi.where(), got)
except ImportError:
    check("without certifi, the default is left to fail with its own message", got is None, got)

print("left alone")
check("Windows reads its own certificate store", pick(default_file=gone, default_dir=gone, platform="win32") is None)
check("SSL_CERT_FILE set by the user", pick(default_file=gone, default_dir=gone, env={"SSL_CERT_FILE": bundle}) is None)
check("SSL_CERT_DIR set by the user", pick(default_file=gone, default_dir=gone, env={"SSL_CERT_DIR": full_dir}) is None)
got = pick(default_file=gone, default_dir=gone, env={"SSL_CERT_FILE": gone})
check("an SSL_CERT_FILE that does not exist is not trusted to work", got == bundle, got)

print("this machine")
check("a context can be built", ssl_context().verify_mode.name == "CERT_REQUIRED")

print()
if failures:
    print(f"{len(failures)} FAILED: " + ", ".join(failures))
    sys.exit(1)
print("all passed")
