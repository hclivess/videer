"""
File utilities for videer
Timestamp preservation and in-place file replacement
"""

import json
import os
import shutil
import platform
import logging
from typing import Any, Optional


def write_json_atomic(path: str, data: Any):
    """
    Write JSON via a temporary file and a rename.

    Everything the app remembers between runs goes through here — the queue autosave, defaults.json, preset
    files. Writing in place means a process killed mid-write (and the queue autosave runs after every single
    file) leaves a truncated file behind, and a truncated settings file is indistinguishable from a corrupt
    one: the next start silently falls back to factory defaults with no way to tell the user why.
    """
    tmp = f"{path}.tmp"
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, indent=4)
    os.replace(tmp, path)


def _set_creation_time_windows(path: str, timestamp: float) -> bool:
    """Set the NTFS creation time of *path* via the Win32 API (no PowerShell round-trip)"""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return False

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ_WRITE = 0x00000001 | 0x00000002
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    # Unix epoch -> Windows FILETIME (100 ns ticks since 1601-01-01)
    ticks = int((timestamp + 11644473600) * 10_000_000)
    ft = FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)

    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.SetFileTime.restype = wintypes.BOOL
    kernel32.SetFileTime.argtypes = [wintypes.HANDLE, ctypes.POINTER(FILETIME),
                                     ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ_WRITE, None,
                                  OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
    if handle == INVALID_HANDLE_VALUE:
        return False
    try:
        return bool(kernel32.SetFileTime(handle, ctypes.byref(ft), None, None))
    finally:
        kernel32.CloseHandle(handle)


def replacement_path(original_file: str, new_file: str) -> str:
    """
    Where *new_file* lands when it takes over from *original_file*: the original's stem with the new file's
    own extension, lower-cased. The extension names the container, so it comes from the file that has the
    container, never from the file being replaced.
    """
    stem = os.path.splitext(original_file)[0]
    return stem + os.path.splitext(new_file)[1].lower()


def backup_path(original_file: str) -> str:
    """
    Where the original goes: <name>.old<ext>, the whole original filename plus a marker, ending in the
    original's own extension so the backup stays openable — tape.avi becomes tape.avi.old.avi.
    """
    return f"{original_file}.old{os.path.splitext(original_file)[1]}"


def _same_path(a: str, b: str) -> bool:
    """Whether two paths name one file: the same spelling, or one inode on a case-insensitive filesystem"""
    if os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b)):
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


class FileOperations:
    """Handles file operations and metadata preservation"""

    def preserve_timestamps(self, source_file: str, dest_file: str,
                            logger: Optional[logging.Logger] = None) -> bool:
        """Copy access/modification (and, on Windows, creation) times from source to dest"""
        try:
            st = os.stat(source_file)
            os.utime(dest_file, (st.st_atime, st.st_mtime))

            if platform.system() == 'Windows':
                if not _set_creation_time_windows(dest_file, st.st_ctime) and logger:
                    logger.warning("Failed to set creation time")

            if logger:
                logger.info(f"Preserved timestamps from {os.path.basename(source_file)}")
            return True
        except Exception as e:
            if logger:
                logger.warning(f"Error preserving timestamps: {e}")
            return False

    @staticmethod
    def output_is_usable(path: str) -> bool:
        """True when *path* exists and is a non-empty regular file"""
        try:
            return os.path.isfile(path) and os.path.getsize(path) > 0
        except OSError:
            return False

    def replace_file_as(self, new_file: str, original_file: str,
                        logger: Optional[logging.Logger] = None,
                        keep_backup: bool = True) -> Optional[str]:
        """
        Put *new_file* where *original_file* was, keeping the original as <name>.old<ext>.

        The destination is always the original's stem with the new file's own extension. Nothing about the
        original's name is carried over except the stem: not its container, not even the way its extension
        happened to be spelt. Encoding an .avi source into MKV and replacing the original used to move
        Matroska onto the .avi name: the file then lies about what it is, which players mostly paper over by
        sniffing the content and other tools do not. So the pair on disk is tape.mkv (new) and
        tape.avi.old.avi (original), and a TAPE.MKV re-encoded into MKV comes back as TAPE.mkv.

        Returns the path the file ended up at, or None if nothing was moved.
        """
        if not self.output_is_usable(new_file):
            if logger:
                logger.error(f"New file is missing or empty: {new_file}")
            return None

        target = replacement_path(original_file, new_file)

        if os.path.exists(target) and not _same_path(target, original_file):
            # Something else already owns that name; overwriting it would be a deletion nobody asked for
            if logger:
                logger.error(f"Not replacing: {target} already exists")
            return None

        try:
            st = os.stat(original_file)
            backup = backup_path(original_file)
            if os.path.exists(backup):
                os.remove(backup)
            os.rename(original_file, backup)
            shutil.move(new_file, target)

            os.utime(target, (st.st_atime, st.st_mtime))
            if platform.system() == 'Windows':
                _set_creation_time_windows(target, st.st_ctime)

            if keep_backup:
                if logger:
                    logger.info(f"Replaced {original_file} with {target} (original kept as {backup})")
            else:
                os.remove(backup)
                if logger:
                    logger.info(f"Replaced {original_file} with {target} (original deleted)")
            return target
        except Exception as e:
            if logger:
                logger.error(f"Error replacing file: {e}")
            return None

    def replace_file(self, new_file: str, original_file: str,
                     logger: Optional[logging.Logger] = None,
                     keep_backup: bool = True) -> bool:
        """replace_file_as for callers that only need to know whether it worked"""
        return self.replace_file_as(new_file, original_file, logger, keep_backup) is not None

    def delete_source(self, source_file: str, output_file: str,
                      logger: Optional[logging.Logger] = None) -> bool:
        """
        Permanently delete *source_file* — but only if *output_file* exists and
        is non-empty, so a failed or interrupted encode never costs the original.
        """
        if not self.output_is_usable(output_file):
            if logger:
                logger.warning(f"Not deleting source: output missing or empty: {output_file}")
            return False
        try:
            os.remove(source_file)
            if logger:
                logger.info(f"Deleted source file: {source_file}")
            return True
        except Exception as e:
            if logger:
                logger.error(f"Error deleting source file: {e}")
            return False
