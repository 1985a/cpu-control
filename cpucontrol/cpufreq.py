"""Ajustes de cpufreq / amd-pstate (boost, EPP, gobernador, frecuencias). Escritura: solo root."""
from __future__ import annotations

from .paths import exists, glob, read, read_int, write

ROOT = "/sys/devices/system/cpu/cpufreq"


def policies() -> list[str]:
    return glob(f"{ROOT}/policy[0-9]*")


def boost_paths() -> list[str]:
    if exists(f"{ROOT}/boost"):
        return [f"{ROOT}/boost"]
    return [f"{p}/boost" for p in policies() if exists(f"{p}/boost")]


def capture() -> dict:
    pols = policies()
    if not pols:
        return {}
    p0 = pols[0]
    bp = boost_paths()
    return {
        "governor": read(f"{p0}/scaling_governor"),
        "epp": read(f"{p0}/energy_performance_preference"),
        "min_mhz": (read_int(f"{p0}/scaling_min_freq") or 0) // 1000 or None,
        "max_mhz": (read_int(f"{p0}/scaling_max_freq") or 0) // 1000 or None,
        "boost": (read(bp[0]) == "1") if bp else None,
    }


def _w(path: str, value: str, errors: list, label: str) -> bool:
    try:
        write(path, value)
        return True
    except OSError as e:
        errors.append(f"{label}: {e.strerror or e}")
        return False


def apply(boost=None, epp=None, governor=None, min_mhz=None, max_mhz=None) -> list[dict]:
    """Devuelve una lista de resultados [{item, ok, msg}]."""
    results = []

    def done(item, errs):
        results.append({"item": item, "ok": not errs, "msg": "; ".join(sorted(set(errs))) or "aplicado"})

    pols = policies()
    if boost is not None:
        errs: list[str] = []
        paths = boost_paths()
        if not paths:
            errs.append("el driver no permite cambiar el boost")
        for p in paths:
            _w(p, "1" if boost else "0", errs, "boost")
        done("Precision Boost", errs)
    if governor is not None:
        errs = []
        for p in pols:
            _w(f"{p}/scaling_governor", governor, errs, "gobernador")
        done("Gobernador", errs)
    if epp is not None:
        errs = []
        gov = read(f"{pols[0]}/scaling_governor") if pols else None
        if gov == "performance" and epp != "performance":
            errs.append("con el gobernador «performance» el EPP queda fijo en performance")
        else:
            for p in pols:
                if exists(f"{p}/energy_performance_preference"):
                    _w(f"{p}/energy_performance_preference", epp, errs, "EPP")
        done("Preferencia de energía (EPP)", errs)
    if min_mhz is not None or max_mhz is not None:
        errs = []
        for p in pols:
            hw_min = read_int(f"{p}/cpuinfo_min_freq") or 0
            if min_mhz is not None:
                _w(f"{p}/scaling_min_freq", str(hw_min), errs, "mínima")   # evita min > max temporal
            if max_mhz is not None:
                _w(f"{p}/scaling_max_freq", str(max_mhz * 1000), errs, "máxima")
            if min_mhz is not None:
                _w(f"{p}/scaling_min_freq", str(min_mhz * 1000), errs, "mínima")
        done("Frecuencias mínima/máxima", errs)
    return results
