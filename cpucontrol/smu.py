"""Acceso al SMU del procesador mediante el módulo ryzen_smu (solo root).

Interfaz del módulo (/sys/kernel/ryzen_smu_drv):
  smu_args   6 palabras de 32 bits little-endian (argumentos / respuesta)
  rsmu_cmd, mp1_smu_cmd   escribir el ID ejecuta el comando; leer devuelve el estado
  smn        escribir 4 bytes = leer esa dirección SMN
  pm_table, pm_table_version   tabla de métricas (floats)
"""
from __future__ import annotations

import math
import struct
import threading
from dataclasses import dataclass

from . import cpudb, pmtables
from .paths import SMU_DIR, P, exists, read, read_bytes

OK = 0x01
STATUS_TEXT = {
    0x00: "esperando", 0x01: "OK", 0xFF: "falló", 0xFE: "comando desconocido",
    0xFD: "rechazado (requisito no cumplido)", 0xFC: "rechazado (ocupado)", 0xFB: "tiempo agotado",
    0xFA: "argumento inválido", 0xF9: "plataforma no soportada", 0xF8: "búfer insuficiente",
    0xF7: "error de mapeo de memoria", 0xF6: "error PCI",
}
MAILBOX_FILE = {"rsmu": "rsmu_cmd", "mp1": "mp1_smu_cmd"}


class SmuError(Exception):
    pass


@dataclass
class Result:
    status: int
    args: tuple

    @property
    def ok(self) -> bool:
        return self.status == OK

    @property
    def text(self) -> str:
        return STATUS_TEXT.get(self.status, f"código 0x{self.status:02X}")


class Smu:
    def __init__(self):
        if not exists(SMU_DIR):
            raise SmuError("ryzen_smu no está cargado")
        self.lock = threading.Lock()
        self.version = read(f"{SMU_DIR}/version")
        self.drv_version = read(f"{SMU_DIR}/drv_version")
        cn = read(f"{SMU_DIR}/codename")
        self.codename = int(cn) if cn and cn.isdigit() else 0

    def has(self, mailbox: str) -> bool:
        return exists(f"{SMU_DIR}/{MAILBOX_FILE[mailbox]}")

    def send(self, mailbox: str, cmd: int, *args: int) -> Result:
        if not cmd:
            return Result(0xFE, ())
        if not self.has(mailbox):
            return Result(0xF9, ())
        words = [int(a) & 0xFFFFFFFF for a in args][:6]
        words += [0] * (6 - len(words))
        with self.lock:
            with open(P(f"{SMU_DIR}/smu_args"), "wb") as f:
                f.write(struct.pack("<6I", *words))
            path = P(f"{SMU_DIR}/{MAILBOX_FILE[mailbox]}")
            with open(path, "wb") as f:
                f.write(struct.pack("<I", cmd))
            with open(path, "rb") as f:
                raw = f.read(4)
            status = struct.unpack("<I", raw.ljust(4, b"\0"))[0] if raw else 0xFF
            with open(P(f"{SMU_DIR}/smu_args"), "rb") as f:
                out = struct.unpack("<6I", f.read(24).ljust(24, b"\0"))
        return Result(status, out)

    def send_pair(self, cmds: dict, style: str, *args: int) -> Result:
        """Usa el orden de buzones de ZenStates-Core: CPU → RSMU y luego MP1; APU → MP1 primero."""
        order = ("mp1", "rsmu") if style == "apu" else ("rsmu", "mp1")
        last = Result(0xFE, ())
        for mb in order:
            if cmds.get(mb):
                last = self.send(mb, cmds[mb], *args)
                if last.ok:
                    return last
        return last

    def smn_read(self, addr: int) -> int | None:
        with self.lock:
            try:
                with open(P(f"{SMU_DIR}/smn"), "wb") as f:
                    f.write(struct.pack("<I", addr))
                with open(P(f"{SMU_DIR}/smn"), "rb") as f:
                    raw = f.read(4)
            except OSError:
                return None
        if len(raw) != 4:
            return None
        return struct.unpack("<I", raw)[0]

    def test(self) -> bool:
        r = self.send("rsmu", cpudb.SMU_TEST_MSG, 0x1234) if self.has("rsmu") else \
            self.send("mp1", cpudb.SMU_TEST_MSG, 0x1234)
        return r.ok and r.args[0] == 0x1235

    def pm_table(self) -> tuple[int | None, bytes | None]:
        raw = read_bytes(f"{SMU_DIR}/pm_table_version")
        ver = struct.unpack("<I", raw[:4])[0] if raw and len(raw) >= 4 else None
        return ver, read_bytes(f"{SMU_DIR}/pm_table")


# --- Mapa de núcleos ----------------------------------------------------
@dataclass
class CoreMap:
    slots: list[tuple[int, int, int]]   # por núcleo lógico: (ccd físico, ccx, núcleo en el ccx)
    verified: bool
    note: str
    slots_per_ccd: int = 8
    slots_per_ccx: int = 8

    def pm_index(self, logical: int) -> int:
        ccd, ccx, core = self.slots[logical]
        return ccd * self.slots_per_ccd + ccx * self.slots_per_ccx + core


def _bits(v: int, hi: int, lo: int) -> int:
    return (v >> lo) & ((1 << (hi - lo + 1)) - 1)


def build_core_map(smu: Smu | None, fuses_key: str | None, n_cores: int, groups: list[list[int]]) -> CoreMap:
    fz = cpudb.FUSES.get(fuses_key) if fuses_key else None
    slots_ccd = (fz.ccx_per_ccd * fz.slots_per_ccx) if fz else 8
    spc = fz.slots_per_ccx if fz else 8

    def fallback(note: str) -> CoreMap:
        slots = []
        if fz and fz.ccx_per_ccd == 2:
            # Zen/Zen+/Zen 2: los grupos de L3 son CCX; dos CCX por CCD
            for gi, g in enumerate(groups):
                for k, _ in enumerate(g):
                    slots.append((gi // 2, gi % 2, k))
        else:
            for gi, g in enumerate(groups):
                for k, _ in enumerate(g):
                    slots.append((gi, 0, k))
        if len(slots) != n_cores:
            slots = [(0, 0, i) for i in range(n_cores)]
        return CoreMap(slots, False, note, slots_ccd, spc)

    if smu is None:
        return fallback("Sin ryzen_smu: no se pueden leer los fusibles de núcleos")
    if fz is None:
        return fallback("Este procesador no tiene mapa de fusibles conocido")

    ccds = [0]
    if fz.ccd_fuse1:
        present, down = smu.smn_read(fz.ccd_fuse1), smu.smn_read(fz.ccd_fuse2)
        if present is None or down is None or present == 0xFFFFFFFF:
            return fallback("No se pudo leer el fusible de CCD")
        dis = _bits(present, 31, 30) | (_bits(down, 5, 0) << 2)
        en = (~dis & 0xFF) if fz.ccd_from_down else (_bits(present, 23, 22) & ~dis)
        ccds = [i for i in range(8) if en & (1 << i)]
        if len(ccds) != len(groups) // fz.ccx_per_ccd and len(ccds) != len(groups):
            return fallback(f"Los fusibles indican {len(ccds)} CCD y el sistema {len(groups)} grupos de L3")
    slots = []
    for phys in ccds:
        val = smu.smn_read(fz.core_fuse + fz.ccd_stride * phys)
        if val is None or val == 0xFFFFFFFF:
            return fallback(f"No se pudo leer el fusible de núcleos del CCD{phys}")
        disabled = (val >> fz.core_shift) & ((1 << slots_ccd) - 1)
        for s in range(slots_ccd):
            if not disabled & (1 << s):
                slots.append((phys, s // spc, s % spc))
    if len(slots) != n_cores:
        return fallback(f"Los fusibles indican {len(slots)} núcleos y el sistema {n_cores}")
    return CoreMap(slots, True, "Mapa leído de los fusibles del procesador", slots_ccd, spc)


# --- Tabla PM --------------------------------------------------------------
def decode_pm(version: int | None, raw: bytes | None, cmap: CoreMap, apu: bool) -> dict | None:
    if version is None or raw is None or version not in pmtables.TABLES:
        return None
    t = pmtables.TABLES[version]
    n = len(raw) // 4
    floats = struct.unpack(f"<{n}f", raw[: n * 4])

    def f(name):
        i = t["scalar"].get(name)
        if i is None or i >= n:
            return None
        v = floats[i]
        return round(v, 3) if math.isfinite(v) else None   # descarta NaN e infinitos

    out = {"version": version, "experimental": bool(t["meta"].get("experimental"))}
    if apu and t["scalar"].get("PPT_LIMIT_FAST") is not None:
        out["ppt"] = [f("PPT_VALUE_FAST"), f("PPT_LIMIT_FAST")]
        out["ppt_slow_limit"] = f("PPT_LIMIT")
        out["stapm_limit"] = f("STAPM_LIMIT")
    else:
        out["ppt"] = [f("PPT_VALUE"), f("PPT_LIMIT")]
    out["tdc"] = [f("TDC_VALUE"), f("TDC_LIMIT")]
    out["edc"] = [f("EDC_VALUE"), f("EDC_LIMIT")]
    out["thm"] = [f("THM_VALUE"), f("THM_LIMIT")]
    out["socket_w"] = f("SOCKET_POWER")
    out["fclk"] = f("FCLK_FREQ")
    out["uclk"] = f("UCLK_FREQ")
    out["mclk"] = f("MEMCLK_FREQ")
    out["vcore"] = f("CPU_TELEMETRY_VOLTAGE")
    out["icore"] = f("CPU_TELEMETRY_CURRENT")
    out["vsoc"] = f("SOC_TELEMETRY_VOLTAGE")
    for src, dst, scale in (("CORE_FREQEFF", "core_freq_eff", 1000), ("CORE_POWER", "core_power", 1),
                            ("CORE_TEMP", "core_temp", 1), ("CORE_VOLTAGE", "core_volt", 1),
                            ("CORE_C0", "core_c0", 1)):
        idxs = t["array"].get(src)
        if not idxs:
            continue
        vals = []
        for logical in range(len(cmap.slots)):
            pi = cmap.pm_index(logical)
            if pi < len(idxs) and idxs[pi] < n:
                v = floats[idxs[pi]]
                vals.append(round(v * scale, 3) if math.isfinite(v) else None)
            else:
                vals.append(None)
        out[dst] = vals
    return out
