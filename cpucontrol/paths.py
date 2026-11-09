"""Acceso a /sys, /proc y /run con un prefijo opcional (CPU_CONTROL_SYSROOT) para pruebas."""
from __future__ import annotations

import glob as _glob
import os

# Las pruebas simulan /sys y /run bajo otra carpeta. Un proceso root (helper, servicio)
# nunca hace caso de esta variable: no debe poder redirigirse adónde lee o escribe.
SYSROOT = "" if os.geteuid() == 0 else os.environ.get("CPU_CONTROL_SYSROOT", "")



def P(path: str) -> str:
    if SYSROOT and path.startswith("/") and not path.startswith(SYSROOT + "/"):
        return SYSROOT + path
    return path


RUN_DIR = P("/run/cpu-control")
STATUS_FILE = f"{RUN_DIR}/status.json"
TELEMETRY_FILE = f"{RUN_DIR}/telemetry.json"
STOCK_FILE = f"{RUN_DIR}/stock.json"
TOUCHED_FILE = f"{RUN_DIR}/touched.json"
SESSION_FILE = f"{RUN_DIR}/session.json"
ETC_DIR = P("/etc/cpu-control")
BOOT_FILE = f"{ETC_DIR}/active.json"
SETTINGS_FILE = f"{ETC_DIR}/settings.json"
SMU_DIR = "/sys/kernel/ryzen_smu_drv"


def read(path: str, default: str | None = None) -> str | None:
    try:
        with open(P(path)) as f:
            return f.read().strip()
    except (OSError, UnicodeDecodeError):
        return default


def read_bytes(path: str) -> bytes | None:
    try:
        with open(P(path), "rb") as f:
            return f.read()
    except OSError:
        return None


def read_int(path: str, default: int | None = None) -> int | None:
    v = read(path)
    if v is None:
        return default
    try:
        return int(v, 0) if v.startswith("0x") else int(v)
    except ValueError:
        return default


def exists(path: str) -> bool:
    return os.path.exists(P(path))


def readable(path: str) -> bool:
    return os.access(P(path), os.R_OK)


def glob(pattern: str) -> list[str]:
    """Devuelve rutas SIN el prefijo SYSROOT, ordenadas de forma natural."""
    res = _glob.glob(P(pattern), recursive=True)
    if SYSROOT:
        res = [r[len(SYSROOT):] for r in res]
    return sorted(res, key=_natural)


def write(path: str, value: str) -> None:
    with open(P(path), "w") as f:
        f.write(value)


def _natural(s: str):
    import re
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def parse_cpulist(s: str | None) -> list[int]:
    out: list[int] = []
    if not s:
        return out
    for raw in s.split(","):
        part = raw.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out
