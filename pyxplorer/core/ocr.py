import os
import shutil
import sys
from typing import Any

try:
    import pytesseract
except Exception:
    pytesseract = None


def _candidate_tesseract_paths() -> list[str]:
    env_path = os.environ.get("TESSERACT_CMD", "").strip()
    candidates: list[str] = []
    if env_path:
        candidates.append(env_path)

    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    user_profile = os.environ.get("USERPROFILE", "").strip()
    if local_appdata:
        candidates.extend(
            [
                os.path.join(local_appdata, "Programs", "Tesseract-OCR", "tesseract.exe"),
                os.path.join(local_appdata, "Tesseract-OCR", "tesseract.exe"),
            ]
        )
    if user_profile:
        candidates.extend(
            [
                os.path.join(user_profile, "AppData", "Local", "Programs", "Tesseract-OCR", "tesseract.exe"),
            ]
        )

    candidates.extend(
        [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
    )
    return candidates


def _read_hkcu_user_path_dirs() -> list[str]:
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except Exception:
        return []

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            value, _ = winreg.QueryValueEx(key, "Path")
    except Exception:
        return []

    if not isinstance(value, str) or not value.strip():
        return []

    result: list[str] = []
    for part in value.split(";"):
        candidate = os.path.expandvars(part.strip())
        if candidate:
            result.append(candidate)
    return result


def _find_in_dirs(dirs: list[str], executable: str) -> str | None:
    seen: set[str] = set()
    for directory in dirs:
        if not directory:
            continue
        norm = os.path.normcase(os.path.normpath(directory))
        if norm in seen:
            continue
        seen.add(norm)
        full = os.path.join(directory, executable)
        if os.path.isfile(full):
            return full
    return None


def _resolve_tesseract_cmd() -> str | None:
    system_cmd = shutil.which("tesseract")
    if system_cmd:
        return system_cmd

    user_path_cmd = _find_in_dirs(_read_hkcu_user_path_dirs(), "tesseract.exe")
    if user_path_cmd:
        return user_path_cmd

    for candidate in _candidate_tesseract_paths():
        if os.path.isfile(candidate):
            return candidate
    return None


def ocr_backend_status() -> tuple[bool, str]:
    if pytesseract is None:
        return False, "pytesseract is not installed"

    cmd = _resolve_tesseract_cmd()
    if cmd is None:
        return False, "tesseract executable not found (PATH or TESSERACT_CMD)"

    pytesseract.pytesseract.tesseract_cmd = cmd
    try:
        version = str(pytesseract.get_tesseract_version())
    except Exception as exc:
        return False, f"tesseract unavailable: {exc}"
    return True, f"Tesseract {version}"


def extract_text_from_image(image: Any, lang: str = "eng") -> str:
    ok, message = ocr_backend_status()
    if not ok:
        raise RuntimeError(message)

    pil_image = image.convert("L")
    width, height = pil_image.size
    if width < 1200:
        scale = max(1.0, 1200.0 / max(1, width))
        if scale > 1.0:
            new_size = (int(width * scale), int(height * scale))
            pil_image = pil_image.resize(new_size)

    text = pytesseract.image_to_string(pil_image, lang=lang)
    return text.strip()
