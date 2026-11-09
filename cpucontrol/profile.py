"""Perfiles de ajuste del procesador y su validación (la usan la interfaz, el helper y el servicio)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

MODES = ("eco", "stock", "pbo", "manual")
CO_MODES = ("off", "all", "per_core")
USER_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "cpu-control")
USER_PROFILES = os.path.join(USER_DIR, "profiles.json")


@dataclass
class Profile:
    name: str = "Personalizado"
    mode: str = "pbo"
    # Límites PBO (None = no tocar)
    ppt: float | None = None        # W
    tdc: float | None = None        # A
    edc: float | None = None        # A
    tctl: int | None = None         # °C
    scalar: int | None = None       # 1–10 ×
    # Curve Optimizer
    co_mode: str = "off"
    co_all: int = 0
    co_cores: list[int] = field(default_factory=list)
    # OC manual
    oc_mhz: int | None = None
    oc_volts: float | None = None
    # cpufreq (None = no tocar)
    boost: bool | None = None
    epp: str | None = None
    governor: str | None = None
    min_mhz: int | None = None
    max_mhz: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Profile:
        p = cls()
        for k in asdict(p):
            if k in d:
                setattr(p, k, d[k])
        p.co_cores = [int(x) for x in (p.co_cores or [])]
        return p


class ValidationError(ValueError):
    pass


def _num(v, label, lo, hi, integer=False):
    if v is None:
        return
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValidationError(f"{label}: valor inválido")
    if integer and int(v) != v:
        raise ValidationError(f"{label}: debe ser un número entero")
    if not (lo <= v <= hi):
        raise ValidationError(f"{label} fuera de rango ({lo:g} a {hi:g})")


def validate(p: Profile, lim: dict) -> Profile:
    """`lim` viene de status.json (lo calcula el servicio con root a partir del hardware real)."""
    feats = lim.get("features", {})
    if not isinstance(p.name, str) or not (1 <= len(p.name) <= 40):
        raise ValidationError("Nombre de perfil inválido")
    if p.mode not in MODES:
        raise ValidationError("Modo inválido")
    if p.mode == "stock":
        return p
    for key, label in (("ppt", "PPT"), ("tdc", "TDC"), ("edc", "EDC")):
        v = getattr(p, key)
        if v is not None:
            if not feats.get("limits"):
                raise ValidationError(f"{label}: no disponible en este equipo")
            _num(v, label, *lim[key])
    if p.tctl is not None:
        if not feats.get("limits"):
            raise ValidationError("Límite de temperatura no disponible")
        _num(p.tctl, "Límite de temperatura", *lim["tctl"], integer=True)
    if p.scalar is not None:
        if not feats.get("scalar"):
            raise ValidationError("Escalar PBO no disponible")
        _num(p.scalar, "Escalar PBO", 1, 10, integer=True)
    if p.co_mode not in CO_MODES:
        raise ValidationError("Modo de Curve Optimizer inválido")
    if p.co_mode != "off":
        if p.mode == "manual":
            raise ValidationError("Curve Optimizer no se usa en modo manual")
        if not feats.get("co"):
            raise ValidationError("Curve Optimizer no disponible en este equipo")
        lo, hi = lim["co"]
        if p.co_mode == "all":
            _num(p.co_all, "Curve Optimizer", lo, hi, integer=True)
        else:
            if not feats.get("co_per_core"):
                raise ValidationError("Curve Optimizer por núcleo no disponible (mapa de núcleos sin verificar)")
            if len(p.co_cores) != lim.get("cores", -1):
                raise ValidationError("Curve Optimizer: número de núcleos incorrecto")
            for i, v in enumerate(p.co_cores):
                _num(v, f"Curve Optimizer núcleo {i}", lo, hi, integer=True)
    if p.mode == "manual":
        if not feats.get("oc"):
            raise ValidationError("OC manual no disponible en este equipo")
        if p.oc_mhz is None or p.oc_volts is None:
            raise ValidationError("OC manual: indica frecuencia y voltaje")
        _num(p.oc_mhz, "Frecuencia manual", *lim["oc_mhz"], integer=True)
        _num(p.oc_volts, "Voltaje manual", *lim["oc_volts"])
    elif p.oc_mhz is not None or p.oc_volts is not None:
        raise ValidationError("Frecuencia/voltaje manual solo en modo Manual")
    # cpufreq
    if p.boost is not None and not isinstance(p.boost, bool):
        raise ValidationError("Boost inválido")
    if p.boost is not None and not feats.get("boost"):
        raise ValidationError("Control de boost no disponible")
    if p.epp is not None and p.epp not in lim.get("epps", []):
        raise ValidationError("Preferencia de energía (EPP) no disponible")
    if p.governor is not None and p.governor not in lim.get("governors", []):
        raise ValidationError("Gobernador no disponible")
    fr = lim.get("freq_mhz")
    for v, label in ((p.min_mhz, "Frecuencia mínima"), (p.max_mhz, "Frecuencia máxima")):
        if v is not None:
            if not fr:
                raise ValidationError(f"{label}: no disponible")
            _num(v, label, fr[0], fr[1], integer=True)
    if p.min_mhz is not None and p.max_mhz is not None and p.min_mhz > p.max_mhz:
        raise ValidationError("La frecuencia mínima es mayor que la máxima")
    return p


# --- Preajustes ------------------------------------------------------------
def preset(mode: str, lim: dict, stock: dict, cores: int) -> Profile:
    names = {"eco": "Eco", "stock": "Fábrica", "pbo": "PBO", "manual": "Manual"}
    p = Profile(name=names[mode], mode=mode)
    feats = lim.get("features", {})
    if mode == "eco" and feats.get("limits") and stock.get("ppt"):
        # Parecido al modo Eco de AMD (105 W → 65 W TDP, 65 W → 45 W)
        p.ppt = round(max(lim["ppt"][0], stock["ppt"] * 0.65))
        if stock.get("tdc"):
            p.tdc = round(max(lim["tdc"][0], stock["tdc"] * 0.72))
        if stock.get("edc"):
            p.edc = round(max(lim["edc"][0], stock["edc"] * 0.72))
        if "balance_power" in lim.get("epps", []):
            p.epp = "balance_power"
    elif mode == "pbo" and feats.get("limits") and stock.get("ppt"):
        p.ppt = round(min(lim["ppt"][1], stock["ppt"] * 1.25))
        if stock.get("tdc"):
            p.tdc = round(min(lim["tdc"][1], stock["tdc"] * 1.2))
        if stock.get("edc"):
            p.edc = round(min(lim["edc"][1], stock["edc"] * 1.2))
        if feats.get("co"):
            p.co_mode = "per_core" if feats.get("co_per_core") else "all"
            p.co_cores = [0] * cores
    elif mode == "manual" and feats.get("oc"):
        hw = lim.get("freq_mhz") or (2000, 4000)
        p.oc_mhz = int(round(hw[1] * 0.95 / 25) * 25)
        p.oc_volts = 1.20
    return p


# --- Archivos del usuario ------------------------------------------------
def load_user_profiles() -> list[Profile]:
    try:
        with open(USER_PROFILES) as f:
            return [Profile.from_dict(d) for d in json.load(f)]
    except (OSError, ValueError, TypeError, AttributeError):
        return []


def save_user_profiles(profiles: list[Profile]) -> None:
    os.makedirs(USER_DIR, exist_ok=True)
    tmp = USER_PROFILES + ".tmp"
    with open(tmp, "w") as f:
        json.dump([p.to_dict() for p in profiles], f, indent=2, ensure_ascii=False)
    os.replace(tmp, USER_PROFILES)
