"""
Checks that a frozen videer does not hand its bundled libraries to the programs it starts (issue #21).

PyInstaller starts the app with LD_LIBRARY_PATH pointing at _internal/, which carries the build machine's
libstdc++. Inherited by the system's ffmpeg on a newer distribution, that is "GLIBCXX_3.4.32 not found" and no
encode at all. utils.childproc puts the user's own value back; this covers that logic. The real thing — a frozen
build started on a newer system than it was built on — is a step in .github/workflows/build.yml.

Run from the repository root:  python scripts/test_child_environment.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.childproc import restore_system_library_path

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


BUNDLE = "/opt/videer/_internal"

print("frozen, the user had no LD_LIBRARY_PATH")
env = {"LD_LIBRARY_PATH": BUNDLE, "PATH": "/usr/bin"}
check("reports a change", restore_system_library_path(env, frozen=True) is True)
check("the bundle path is gone", "LD_LIBRARY_PATH" not in env, env)
check("nothing else is touched", env == {"PATH": "/usr/bin"}, env)

print("frozen, the user had their own LD_LIBRARY_PATH")
env = {"LD_LIBRARY_PATH": BUNDLE + ":/home/me/lib", "LD_LIBRARY_PATH_ORIG": "/home/me/lib"}
restore_system_library_path(env, frozen=True)
check("their value is back", env["LD_LIBRARY_PATH"] == "/home/me/lib", env)
check("the bundle is not in it", BUNDLE not in env["LD_LIBRARY_PATH"], env)

print("frozen, already clean")
env = {"PATH": "/usr/bin"}
check("no change reported", restore_system_library_path(env, frozen=True) is False)
check("nothing added", env == {"PATH": "/usr/bin"}, env)

print("from source")
env = {"LD_LIBRARY_PATH": "/home/me/lib"}
check("no change reported", restore_system_library_path(env, frozen=False) is False)
check("the user's LD_LIBRARY_PATH is left alone", env == {"LD_LIBRARY_PATH": "/home/me/lib"}, env)

print("this process")
check("os.environ carries no bundle path", "_internal" not in os.environ.get("LD_LIBRARY_PATH", ""))

print()
if failures:
    print(f"{len(failures)} FAILED: " + ", ".join(failures))
    sys.exit(1)
print("all passed")
