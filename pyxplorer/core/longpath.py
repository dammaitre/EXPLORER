import sys
import os
import pathlib

WIN_MAX_PATH = 260
NTFS_MAX     = 32_767   # hard ceiling for \\?\ extended paths
UNC_PREFIX   = "\\\\?\\"  # Extended-length path prefix  (\\?\)


_ARCHIVE_SEP = "\x00"   # same constant as archive.ARCHIVE_SEP — avoid circular import


def normalize(path: "str | pathlib.Path") -> str:
    """
    Return a path string safe for all Win32 API calls.
    - On Windows: prepend \\?\\ for absolute paths ≥ 240 chars (safety margin).
    - Paths that would exceed the NTFS hard limit (32 767 chars) are returned
      un-prefixed; callers already wrap OS calls in try/except.
    - On other OS: return as-is.
    - Virtual archive paths (containing \\x00) are passed through unchanged.
    Idempotent: safe to call on already-prefixed paths.
    """
    p = str(path)
    if _ARCHIVE_SEP in p:
        return p          # virtual archive path — never pass to OS APIs directly
    if sys.platform != "win32":
        return p
    if p.startswith(UNC_PREFIX):
        return p
    # Fast-path: already an absolute drive path — skip the os.path.abspath() syscall.
    if len(p) >= 3 and p[1] == ":" and p[2] in "/\\":
        abs_p = p.replace("/", "\\")
    else:
        abs_p = os.path.abspath(p)
    # Guard: \\?\ + path must not exceed NTFS_MAX
    if len(UNC_PREFIX) + len(abs_p) > NTFS_MAX:
        # Return without prefix — the path is unusably long; let the caller's
        # try/except handle the OS error gracefully.
        return abs_p
    if len(abs_p) >= WIN_MAX_PATH - 20:  # 20-char safety margin
        return UNC_PREFIX + abs_p
    return abs_p


def to_display(path: str) -> str:
    """Strip \\\\?\\ prefix for display in UI — users should never see it.
    For virtual archive paths, renders the null-byte separator as os.sep
    so the path looks like a normal filesystem path in the UI.
    """
    if _ARCHIVE_SEP in path:
        archive, inner = path.split(_ARCHIVE_SEP, 1)
        display_archive = to_display(archive)
        if inner:
            return display_archive + os.sep + inner.replace("/", os.sep)
        return display_archive
    if path.startswith(UNC_PREFIX):
        return path[len(UNC_PREFIX):]
    return path


def enable_longpath_registry() -> bool:
    """
    Attempt to set HKLM LongPathsEnabled = 1.
    Requires admin. Returns True on success, False if insufficient privileges.
    """
    if sys.platform != "win32":
        return True
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\FileSystem",
            0, winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(key, "LongPathsEnabled", 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(key)
        return True
    except PermissionError:
        return False
