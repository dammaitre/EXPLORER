import os
import subprocess
import sys
from pathlib import Path
from .appdirs import pyxplorer_data_dir


def scripts_dir() -> Path:
    return pyxplorer_data_dir() / "scripts"


def list_heuristic_scripts() -> list[Path]:
    base = scripts_dir()
    if not base.exists():
        return []
    scripts = [p for p in base.glob("*.py") if p.is_file()]
    scripts.sort(key=lambda p: p.name.lower())
    return scripts


def run_heuristic(script_path: str, target_path: str) -> str:
    proc = subprocess.run(
        [sys.executable, script_path, target_path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode == 0:
        out = (proc.stdout or "").strip()
        return out.splitlines()[0] if out else ""
    err = (proc.stderr or "").strip()
    if err:
        return f"ERR: {err.splitlines()[0][:120]}"
    return f"ERR: exit {proc.returncode}"
