"""Base de datos de procesadores Ryzen: generación, código, socket y comandos del SMU.

Los IDs de comandos del SMU vienen de ZenStates-Core (irusanov, GPL-3.0), la referencia
más usada para estos procesadores. Las plataformas que su autor marca como inciertas, o
que no tienen validación pública, quedan como «experimental»: la app no las usa salvo
que el usuario active las funciones experimentales.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Platform:
    codename: str
    generation: str            # "Zen 3"
    socket: str                # "AM4"
    process: str
    kind: str                  # "desktop" | "apu" | "hedt" | "mobile"
    smu: str | None = None     # clave en SMU_PROFILES
    fuses: str | None = None   # clave en FUSES


# ryzen_smu: /sys/kernel/ryzen_smu_drv/codename → nombre
SMU_CODENAMES = {
    1: "Colfax", 2: "Renoir", 3: "Picasso", 4: "Matisse", 5: "Threadripper", 6: "Castle Peak",
    7: "Raven Ridge", 8: "Raven Ridge 2", 9: "Summit Ridge", 10: "Pinnacle Ridge", 11: "Rembrandt",
    12: "Vermeer", 13: "Van Gogh", 14: "Cezanne", 15: "Milan", 16: "Dali", 17: "Lucienne",
    18: "Naples", 19: "Chagall", 20: "Raphael", 21: "Phoenix", 22: "Strix Point", 23: "Granite Ridge",
    24: "Hawk Point", 25: "Storm Peak", 26: "Strix Halo",
}

PLATFORMS = {
    "Summit Ridge":   Platform("Summit Ridge", "Zen", "AM4", "14 nm", "desktop", "zen1", "zen1"),
    "Whitehaven":     Platform("Whitehaven", "Zen", "TR4", "14 nm", "hedt", None, None),
    "Pinnacle Ridge": Platform("Pinnacle Ridge", "Zen+", "AM4", "12 nm", "desktop", "zenp", "zen1"),
    "Colfax":         Platform("Colfax", "Zen+", "TR4", "12 nm", "hedt", None, None),
    "Raven Ridge":    Platform("Raven Ridge", "Zen", "AM4 / FP5", "14 nm", "apu", "apu0", None),
    "Raven Ridge 2":  Platform("Raven Ridge 2", "Zen", "FP5", "14 nm", "mobile", "apu0", None),
    "Picasso":        Platform("Picasso", "Zen+", "AM4 / FP5", "12 nm", "apu", "apu0p", None),
    "Dali":           Platform("Dali", "Zen", "FP5", "14 nm", "mobile", "apu0p", None),
    "Matisse":        Platform("Matisse", "Zen 2", "AM4", "7 nm", "desktop", "zen2", "zen2"),
    "Castle Peak":    Platform("Castle Peak", "Zen 2", "sTRX4", "7 nm", "hedt", "zen2", "zen2"),
    "Renoir":         Platform("Renoir", "Zen 2", "AM4 / FP6", "7 nm", "apu", "apu1", None),
    "Lucienne":       Platform("Lucienne", "Zen 2", "FP6", "7 nm", "mobile", "apu1", None),
    "Van Gogh":       Platform("Van Gogh", "Zen 2", "FF3", "7 nm", "mobile", None, None),
    "Mendocino":      Platform("Mendocino", "Zen 2", "FT6", "6 nm", "mobile", None, None),
    "Vermeer":        Platform("Vermeer", "Zen 3", "AM4", "7 nm", "desktop", "zen3", "zen3"),
    "Chagall":        Platform("Chagall", "Zen 3", "sWRX8", "7 nm", "hedt", "zen3", "zen3"),
    "Cezanne":        Platform("Cezanne", "Zen 3", "AM4 / FP6", "7 nm", "apu", "cezanne", "cezanne"),
    "Barcelo":        Platform("Barcelo", "Zen 3", "FP6", "7 nm", "mobile", "cezanne", "cezanne"),
    "Rembrandt":      Platform("Rembrandt", "Zen 3+", "FP7", "6 nm", "mobile", "rembrandt", None),
    "Raphael":        Platform("Raphael", "Zen 4", "AM5", "5 nm", "desktop", "zen4", "zen4"),
    "Dragon Range":   Platform("Dragon Range", "Zen 4", "FL1", "5 nm", "mobile", "zen4", "zen4"),
    "Storm Peak":     Platform("Storm Peak", "Zen 4", "sTR5", "5 nm", "hedt", "zen4", None),
    "Phoenix":        Platform("Phoenix", "Zen 4", "AM5 / FP7", "4 nm", "apu", "phoenix", "phoenix"),
    "Hawk Point":     Platform("Hawk Point", "Zen 4", "AM5 / FP8", "4 nm", "apu", "phoenix", "phoenix"),
    "Phoenix 2":      Platform("Phoenix 2", "Zen 4 / 4c", "AM5 / FP7", "4 nm", "apu", "phoenix", None),
    "Granite Ridge":  Platform("Granite Ridge", "Zen 5", "AM5", "4 nm", "desktop", "zen5", "zen5"),
    "Strix Point":    Platform("Strix Point", "Zen 5", "FP8", "4 nm", "mobile", None, None),
    "Strix Halo":     Platform("Strix Halo", "Zen 5", "FP11", "4 nm", "mobile", None, None),
    "Krackan Point":  Platform("Krackan Point", "Zen 5", "FP8", "4 nm", "mobile", None, None),
    "Shimada Peak":   Platform("Shimada Peak", "Zen 5", "sTR5", "4 nm", "hedt", None, None),
}

# (familia, modelo) → codename. Rango de modelos cuando aplica.
_MODELS = [
    (0x17, range(0x08), "Summit Ridge"),
    (0x17, range(0x08, 0x10), "Pinnacle Ridge"),
    (0x17, range(0x10, 0x18), "Raven Ridge"),
    (0x17, range(0x18, 0x20), "Picasso"),
    (0x17, range(0x20, 0x30), "Dali"),
    (0x17, range(0x30, 0x40), "Castle Peak"),
    (0x17, range(0x60, 0x68), "Renoir"),
    (0x17, range(0x68, 0x70), "Lucienne"),
    (0x17, range(0x70, 0x80), "Matisse"),
    (0x17, range(0x90, 0x98), "Van Gogh"),
    (0x17, range(0xA0, 0xB0), "Mendocino"),
    (0x19, range(0x08, 0x10), "Chagall"),
    (0x19, range(0x18, 0x20), "Storm Peak"),
    (0x19, range(0x20, 0x30), "Vermeer"),
    (0x19, range(0x40, 0x50), "Rembrandt"),
    (0x19, range(0x50, 0x60), "Cezanne"),
    (0x19, range(0x60, 0x70), "Raphael"),
    (0x19, range(0x70, 0x78), "Phoenix"),
    (0x19, range(0x78, 0x80), "Phoenix 2"),
    (0x1A, range(0x08, 0x10), "Shimada Peak"),
    (0x1A, range(0x20, 0x30), "Strix Point"),
    (0x1A, range(0x40, 0x50), "Granite Ridge"),
    (0x1A, range(0x60, 0x70), "Krackan Point"),
    (0x1A, range(0x70, 0x80), "Strix Halo"),
]


def detect(family: int, model: int, model_name: str = "", smu_codename: int | None = None) -> Platform | None:
    name = None
    if smu_codename and smu_codename in SMU_CODENAMES:
        name = SMU_CODENAMES[smu_codename]
    if name is None:
        for fam, models, cn in _MODELS:
            if fam == family and model in models:
                name = cn
                break
    if name is None:
        return None
    # Ajustes por nombre comercial (mismo modelo CPUID, distinto producto)
    mn = model_name.lower()
    if name == "Raphael" and ("hx" in mn or "laptop" in mn):
        name = "Dragon Range"
    if name == "Phoenix" and any(t in mn for t in ("8600g", "8700g", "8500g", "8300g", "8700f", "8400f")):
        name = "Phoenix"
    if name == "Cezanne" and any(t in mn for t in ("5625u", "5825u", "5425u", "5125c")):
        name = "Barcelo"
    if name == "Threadripper":
        name = "Castle Peak"
    return PLATFORMS.get(name, Platform(name, "Zen", "?", "?", "desktop"))


# --- Comandos del SMU ----------------------------------------------------
# style "cpu": se intenta RSMU y, si falla, MP1.  style "apu": MP1 primero (como hace ZenStates-Core).
# Límites: argumento en mW / mA. Tctl: °C. Escalar PBO: ×100.
# CO: argumento = máscara de núcleo | margen (16 bits, complemento a dos).

@dataclass(frozen=True)
class SmuProfile:
    key: str
    style: str
    ppt: dict = field(default_factory=dict)         # {"rsmu": id, "mp1": id}
    ppt_slow: dict = field(default_factory=dict)    # APU: límite lento
    ppt_stapm: dict = field(default_factory=dict)   # APU: STAPM
    tdc: dict = field(default_factory=dict)
    edc: dict = field(default_factory=dict)
    tctl: dict = field(default_factory=dict)
    scalar: dict = field(default_factory=dict)
    oc_enable: dict = field(default_factory=dict)
    oc_disable: dict = field(default_factory=dict)
    oc_freq_all: dict = field(default_factory=dict)
    oc_vid: dict = field(default_factory=dict)
    co_set: dict = field(default_factory=dict)
    co_set_all: dict = field(default_factory=dict)
    co_get_rsmu: int = 0
    co_range: int = 0          # 0 = sin Curve Optimizer
    svi: int = 2               # 2: V = 1.55 − vid·0.00625   3: V = 0.245 + vid·0.005
    limits_experimental: bool = False
    co_experimental: bool = False
    oc_experimental: bool = False
    notes: str = ""


SMU_PROFILES = {
    "zen1": SmuProfile(
        "zen1", "cpu",
        oc_enable={"rsmu": 0x63, "mp1": 0x37}, oc_disable={"rsmu": 0x18, "mp1": 0x37},
        oc_freq_all={"mp1": 0x39}, oc_vid={"rsmu": 0x17, "mp1": 0x38},
        oc_experimental=True,
        notes="Zen 1 no tiene PBO ni Curve Optimizer; solo OC manual (experimental)."),
    "zenp": SmuProfile(
        "zenp", "cpu",
        ppt={"rsmu": 0x64}, tdc={"rsmu": 0x65}, edc={"rsmu": 0x66}, tctl={"rsmu": 0x68},
        scalar={"rsmu": 0x6A},
        oc_enable={"rsmu": 0x6B}, oc_disable={"mp1": 0x37}, oc_freq_all={"rsmu": 0x6C}, oc_vid={"rsmu": 0x6E},
        limits_experimental=True, oc_experimental=True,
        notes="Zen+ no tiene Curve Optimizer. Los IDs de límites están sin confirmar."),
    "zen2": SmuProfile(
        "zen2", "cpu",
        ppt={"rsmu": 0x53, "mp1": 0x3C}, tdc={"rsmu": 0x54, "mp1": 0x3A}, edc={"rsmu": 0x55, "mp1": 0x3B},
        tctl={"rsmu": 0x56, "mp1": 0x3D}, scalar={"rsmu": 0x58, "mp1": 0x2F},
        oc_enable={"rsmu": 0x5A, "mp1": 0x24}, oc_disable={"rsmu": 0x5B, "mp1": 0x25},
        oc_freq_all={"rsmu": 0x5C, "mp1": 0x26}, oc_vid={"rsmu": 0x61, "mp1": 0x28},
        notes="Zen 2 no tiene Curve Optimizer (llegó con Zen 3)."),
    "zen3": SmuProfile(
        "zen3", "cpu",
        ppt={"rsmu": 0x53, "mp1": 0x3D}, tdc={"rsmu": 0x54, "mp1": 0x3B}, edc={"rsmu": 0x55, "mp1": 0x3C},
        tctl={"rsmu": 0x56, "mp1": 0x3E}, scalar={"rsmu": 0x58, "mp1": 0x2F},
        oc_enable={"rsmu": 0x5A, "mp1": 0x24}, oc_disable={"rsmu": 0x5B, "mp1": 0x25},
        oc_freq_all={"rsmu": 0x5C, "mp1": 0x26}, oc_vid={"rsmu": 0x61, "mp1": 0x28},
        co_set={"mp1": 0x35}, co_set_all={"mp1": 0x36}, co_get_rsmu=0x7C, co_range=30),
    "cezanne": SmuProfile(
        "cezanne", "apu",
        ppt={"mp1": 0x15, "rsmu": 0x32}, ppt_slow={"mp1": 0x16, "rsmu": 0x33}, ppt_stapm={"mp1": 0x14, "rsmu": 0x31},
        tdc={"mp1": 0x1A, "rsmu": 0x38}, edc={"mp1": 0x1C, "rsmu": 0x3A}, tctl={"mp1": 0x19, "rsmu": 0x37},
        scalar={"rsmu": 0x3F, "mp1": 0x49},
        oc_enable={"mp1": 0x2F, "rsmu": 0x17}, oc_disable={"mp1": 0x30, "rsmu": 0x18},
        oc_freq_all={"mp1": 0x31, "rsmu": 0x19}, oc_vid={"mp1": 0x33, "rsmu": 0x1B},
        co_set={"mp1": 0x54}, co_set_all={"mp1": 0x55}, co_get_rsmu=0xC3, co_range=30),
    "apu1": SmuProfile(
        "apu1", "apu",
        ppt={"mp1": 0x15, "rsmu": 0x32}, ppt_slow={"mp1": 0x16, "rsmu": 0x33}, ppt_stapm={"mp1": 0x14, "rsmu": 0x31},
        tdc={"mp1": 0x1A, "rsmu": 0x38}, edc={"mp1": 0x1C, "rsmu": 0x3A}, tctl={"mp1": 0x19, "rsmu": 0x37},
        scalar={"rsmu": 0x3F, "mp1": 0x49},
        oc_enable={"mp1": 0x2F, "rsmu": 0x17}, oc_disable={"mp1": 0x30, "rsmu": 0x18},
        oc_freq_all={"mp1": 0x31, "rsmu": 0x19}, oc_vid={"mp1": 0x33, "rsmu": 0x1B},
        notes="Renoir / Lucienne son Zen 2: sin Curve Optimizer."),
    "apu0": SmuProfile(
        "apu0", "apu",
        ppt={"mp1": 0x1B, "rsmu": 0x2F}, ppt_slow={"mp1": 0x1C, "rsmu": 0x30}, ppt_stapm={"mp1": 0x1A, "rsmu": 0x2E},
        tdc={"mp1": 0x20, "rsmu": 0x34}, edc={"mp1": 0x22, "rsmu": 0x36}, tctl={"mp1": 0x1F, "rsmu": 0x33},
        limits_experimental=True,
        notes="Raven Ridge: OC bloqueado por AMD en el SMU; solo límites (experimental)."),
    "apu0p": SmuProfile(
        "apu0p", "apu",
        ppt={"mp1": 0x1B, "rsmu": 0x2F}, ppt_slow={"mp1": 0x1C, "rsmu": 0x30}, ppt_stapm={"mp1": 0x1A, "rsmu": 0x2E},
        tdc={"mp1": 0x20, "rsmu": 0x34}, edc={"mp1": 0x22, "rsmu": 0x36}, tctl={"mp1": 0x1F, "rsmu": 0x33},
        scalar={"rsmu": 0x7C, "mp1": 0x57},
        oc_enable={"rsmu": 0x78, "mp1": 0x58}, oc_freq_all={"rsmu": 0x79, "mp1": 0x59},
        oc_vid={"rsmu": 0x7B, "mp1": 0x5B},
        limits_experimental=True, oc_experimental=True),
    "rembrandt": SmuProfile(
        "rembrandt", "apu",
        ppt={"mp1": 0x15, "rsmu": 0x32}, ppt_slow={"mp1": 0x16, "rsmu": 0x33}, ppt_stapm={"mp1": 0x14, "rsmu": 0x31},
        tdc={"mp1": 0x1A, "rsmu": 0x38}, edc={"mp1": 0x1C, "rsmu": 0x3A}, tctl={"mp1": 0x19, "rsmu": 0x37},
        co_set={"mp1": 0x4B}, co_set_all={"mp1": 0x4C}, co_get_rsmu=0x2F, co_range=30,
        limits_experimental=True, co_experimental=True),
    "phoenix": SmuProfile(
        "phoenix", "apu",
        ppt={"mp1": 0x15, "rsmu": 0x32}, ppt_slow={"mp1": 0x16, "rsmu": 0x33}, ppt_stapm={"mp1": 0x14, "rsmu": 0x31},
        tdc={"mp1": 0x1A, "rsmu": 0x38}, edc={"mp1": 0x1C, "rsmu": 0x3A}, tctl={"mp1": 0x19, "rsmu": 0x37},
        scalar={"rsmu": 0x3E, "mp1": 0x63},
        oc_enable={"mp1": 0x57, "rsmu": 0x17}, oc_disable={"mp1": 0x58, "rsmu": 0x18},
        oc_freq_all={"mp1": 0x59, "rsmu": 0x19}, oc_vid={"mp1": 0x5B, "rsmu": 0x1B},
        co_set={"mp1": 0x4B}, co_set_all={"mp1": 0x4C}, co_get_rsmu=0xE1, co_range=50, svi=3,
        oc_experimental=True),
    "zen4": SmuProfile(
        "zen4", "cpu",
        ppt={"rsmu": 0x56, "mp1": 0x3E}, tdc={"rsmu": 0x57, "mp1": 0x3C}, edc={"rsmu": 0x58, "mp1": 0x3D},
        tctl={"rsmu": 0x59, "mp1": 0x3F}, scalar={"rsmu": 0x5B, "mp1": 0x2F},
        oc_enable={"rsmu": 0x5D, "mp1": 0x24}, oc_disable={"rsmu": 0x5E, "mp1": 0x25},
        oc_freq_all={"rsmu": 0x5F, "mp1": 0x26}, oc_vid={"rsmu": 0x61, "mp1": 0x28},
        co_set={"mp1": 0x35, "rsmu": 0x6}, co_set_all={"mp1": 0x36, "rsmu": 0x7}, co_get_rsmu=0xD5,
        co_range=50, svi=3, oc_experimental=True),
    "zen5": SmuProfile(
        "zen5", "cpu",
        ppt={"rsmu": 0x56, "mp1": 0x3E}, tdc={"rsmu": 0x57, "mp1": 0x3C}, edc={"rsmu": 0x58, "mp1": 0x3D},
        tctl={"rsmu": 0x59, "mp1": 0x3F}, scalar={"rsmu": 0x5B, "mp1": 0x2F},
        oc_enable={"rsmu": 0x5D, "mp1": 0x24}, oc_disable={"rsmu": 0x5E, "mp1": 0x25},
        oc_freq_all={"rsmu": 0x5F, "mp1": 0x26}, oc_vid={"rsmu": 0x61, "mp1": 0x28},
        co_set={"mp1": 0x35, "rsmu": 0x6}, co_set_all={"mp1": 0x36, "rsmu": 0x7}, co_get_rsmu=0xD5,
        co_range=50, svi=3, oc_experimental=True),
}

SMU_TEST_MSG = 0x1  # TestMessage: el SMU responde arg + 1


# --- Fusibles de núcleos (para saber qué núcleo físico es cada núcleo lógico) ---
# Direcciones SMN de ZenStates-Core (verificadas por su autor en los chips indicados).
@dataclass(frozen=True)
class Fuses:
    ccd_fuse1: int = 0
    ccd_fuse2: int = 0
    ccd_from_down: bool = False
    core_fuse: int = 0
    core_shift: int = 0
    ccd_stride: int = 0
    ccx_per_ccd: int = 1
    slots_per_ccx: int = 8


_STRIDE = 1 << 25
FUSES = {
    "zen1":     Fuses(core_fuse=0x5D25C, ccx_per_ccd=2, slots_per_ccx=4),
    "zen2":     Fuses(0x5D218, 0x5D21C, True, 0x30081A38, 0, _STRIDE, 2, 4),
    "zen3":     Fuses(0x5D218, 0x5D21C, True, 0x30081D98, 0, _STRIDE, 1, 8),
    "zen4":     Fuses(0x5D3BC, 0x5D3C0, False, 0x30081CD0, 0, _STRIDE, 1, 8),
    "zen5":     Fuses(0x5D3BC, 0x5D3C0, False, 0x304A03DC, 0, _STRIDE, 1, 8),
    "cezanne":  Fuses(core_fuse=0x5D448, core_shift=11, ccx_per_ccd=1, slots_per_ccx=8),
    "phoenix":  Fuses(core_fuse=0x5D528, core_shift=7, ccx_per_ccd=1, slots_per_ccx=8),
}


def core_mask(family: int, ccd: int, ccx: int, core: int) -> int:
    """Máscara de núcleo para comandos por núcleo (Curve Optimizer)."""
    if family > 0x17:
        return (ccd << 28) | ((core % 8) << 20)
    return (ccd << 28) | ((ccx % 2) << 24) | ((core % 4) << 20)


def co_arg(mask: int, margin: int) -> int:
    return (mask & 0xFFF00000) | (margin & 0xFFFF)


def decode_co(value: int) -> int:
    value &= 0xFFFFFFFF
    if value & 0x80000000:
        return value - (1 << 32)
    v = value & 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def voltage_to_vid(volts: float, svi: int) -> int:
    """Redondea siempre hacia el voltaje MENOR, para no pasar nunca del valor pedido."""
    import math
    if svi == 3:
        return max(0, min(0xFF, math.floor((volts - 0.245) / 0.005 + 1e-9)))
    return max(0, min(0xFF, math.ceil((1.55 - volts) / 0.00625 - 1e-9)))


def vid_to_voltage(vid: int, svi: int) -> float:
    return round(0.245 + vid * 0.005, 4) if svi == 3 else round(1.55 - vid * 0.00625, 4)


def max_manual_voltage(generation: str | None) -> float:
    """Tope de voltaje fijo para OC manual (carga sostenida en todos los núcleos).

    Zen 2 / Zen 3 (7 nm): 1.30 V · Zen 4 / Zen 5 (5/4 nm): 1.20 V — los valores de la guía de
    michaelnager (r/ryzen), conservadores frente a los ~1.45 V que AMD usa en boost de un núcleo.
    Zen / Zen+ (14/12 nm): 1.35 V.
    """
    g = generation or ""
    if g.startswith(("Zen 4", "Zen 5")):
        return 1.20
    if g.startswith(("Zen 2", "Zen 3")):
        return 1.30
    return 1.35
