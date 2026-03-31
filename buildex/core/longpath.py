import sys
import os
import pathlib

WIN_MAX_PATH = 260
UNC_PREFIX = "\\\\?\\"  # Extended-length path prefix  (\\?\)


def normalize(path: "str | pathlib.Path") -> str:
    """
    Return a path string safe for all Win32 API calls.
    - On Windows: prepend \\?\\ for absolute paths longer than 240 chars (safety margin).
    - On other OS: return as-is.
    Idempotent: safe to call on already-prefixed paths.
    """
    p = str(path)
    if sys.platform != "win32":
        return p
    if p.startswith(UNC_PREFIX):
        return p
    abs_p = os.path.abspath(p)
    if len(abs_p) >= WIN_MAX_PATH - 20:  # 20-char safety margin
        return UNC_PREFIX + abs_p
    return abs_p


def to_display(path: str) -> str:
    """Strip \\\\?\\ prefix for display in UI — users should never see it."""
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
