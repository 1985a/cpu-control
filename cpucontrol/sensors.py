"""Lecturas en vivo sin root: uso y frecuencia por hilo, hwmon (k10temp / zenpower) y la
telemetría que publica el servicio (RAPL y tabla PM del SMU)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .paths import TELEMETRY_FILE, P, glob, read, read_int
from .system import SystemInfo

TELEMETRY_MAX_AGE = 5.0


@dataclass
class CoreSample:
    freq_mhz: float | None = None
    usage: list[float] = field(default_factory=list)   # por hilo, %
    power_w: float | None = None
    temp_c: float | None = None
    volt: float | None = None
    c0: float | None = None


@dataclass
class Sample:
    t: float
    cores: list[CoreSample] = field(default_factory=list)
    usage_total: float | None = None
    freq_avg: float | None = None       # MHz, núcleos activos
    freq_max: float | None = None
    tctl: float | None = None
    tdie: float | None = None
    tccd: list[float] = field(default_factory=list)
    vcore: float | None = None
    vsoc: float | None = None
    icore: float | None = None
    isoc: float | None = None
    p_svi2: float | None = None          # W (núcleo + SoC, zenpower)
    package_w: float | None = None       # RAPL
    socket_w: float | None = None        # tabla PM
    ppt: tuple | None = None             # (valor, límite)
    tdc: tuple | None = None
    edc: tuple | None = None
    thm: tuple | None = None
    fclk: float | None = None
    uclk: float | None = None
    mclk: float | None = None
    sources: dict = field(default_factory=dict)

    @property
    def power(self) -> float | None:
        if self.package_w is not None:
            return self.package_w
        if self.socket_w is not None:
            return self.socket_w
        if self.ppt:
            return self.ppt[0]
        return self.p_svi2

    @property
    def temp(self) -> float | None:
        return self.tctl if self.tctl is not None else self.tdie

    def limiter(self) -> str | None:
        """Qué límite está frenando al procesador (≥ 95 % de su tope)."""
        worst, label = 0.95, None
        for name, pair in (("PPT (potencia)", self.ppt), ("TDC (corriente sostenida)", self.tdc),
                           ("EDC (corriente pico)", self.edc), ("Temperatura", self.thm)):
            if pair and pair[0] is not None and pair[1]:
                r = pair[0] / pair[1]
                if r >= worst:
                    worst, label = r, name
        return label


def _hwmon_values(path: str) -> dict[str, float]:
    """{etiqueta: valor en unidades SI} para temp/in/curr/power."""
    out = {}
    scale = {"temp": 1000, "in": 1000, "curr": 1000, "power": 1_000_000}
    for lab in glob(f"{path}/*_label"):
        kind = lab.rsplit("/", 1)[1].split("_")[0]
        base = "".join(ch for ch in kind if not ch.isdigit())
        if base not in scale:
            continue
        label = read(lab)
        v = read_int(lab.replace("_label", "_input"))
        if label and v is not None:
            out[label] = v / scale[base]
    return out


class Sampler:
    def __init__(self, si: SystemInfo):
        self.si = si
        self._prev_stat: dict[int, tuple[int, int]] = {}
        self._read_stat()

    def _read_stat(self) -> dict[int, float]:
        usage = {}
        try:
            with open(P("/proc/stat")) as f:
                lines = f.readlines()
        except OSError:
            return usage
        for line in lines:
            if not line.startswith("cpu") or line.startswith("cpu "):
                continue
            parts = line.split()
            n = int(parts[0][3:])
            vals = list(map(int, parts[1:]))
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            total = sum(vals[:8])
            prev = self._prev_stat.get(n)
            if prev:
                dt, di = total - prev[0], idle - prev[1]
                usage[n] = max(0.0, min(100.0, 100.0 * (1 - di / dt))) if dt > 0 else 0.0
            self._prev_stat[n] = (total, idle)
        return usage

    def telemetry(self) -> dict:
        try:
            with open(P(TELEMETRY_FILE)) as f:
                data = json.load(f)
            if time.time() - data.get("time", 0) <= TELEMETRY_MAX_AGE:
                return data
        except (OSError, ValueError):
            pass
        return {}

    def sample(self) -> Sample:
        s = Sample(t=time.monotonic())
        usage = self._read_stat()
        tel = self.telemetry()
        pm = tel.get("pm") or {}

        freqs = {}
        for c in self.si.cores:
            for th in c.threads:
                v = read_int(f"/sys/devices/system/cpu/cpu{th}/cpufreq/scaling_cur_freq")
                if v:
                    freqs[th] = v / 1000
        per = {k: pm.get(k) or [] for k in ("core_freq_eff", "core_power", "core_temp", "core_volt", "core_c0")}
        for c in self.si.cores:
            cs = CoreSample(usage=[usage.get(th, 0.0) for th in c.threads])
            i = c.index
            fe = per["core_freq_eff"][i] if i < len(per["core_freq_eff"]) else None
            if fe:
                cs.freq_mhz = fe
            else:
                fs = [freqs[th] for th in c.threads if th in freqs]
                cs.freq_mhz = max(fs) if fs else None
            for attr, key in (("power_w", "core_power"), ("temp_c", "core_temp"), ("volt", "core_volt"),
                              ("c0", "core_c0")):
                arr = per[key]
                if i < len(arr) and arr[i] is not None:
                    setattr(cs, attr, arr[i])
            s.cores.append(cs)
        if usage:
            s.usage_total = sum(usage.values()) / len(usage)
        active = [c.freq_mhz for c in s.cores if c.freq_mhz and max(c.usage or [0]) > 5]
        allf = [c.freq_mhz for c in s.cores if c.freq_mhz]
        s.freq_avg = sum(active) / len(active) if active else (sum(allf) / len(allf) if allf else None)
        s.freq_max = max(allf) if allf else None
        s.sources["freq"] = "SMU" if per["core_freq_eff"] else "cpufreq"

        hw_name = "zenpower" if "zenpower" in self.si.hwmon else ("k10temp" if "k10temp" in self.si.hwmon else None)
        if hw_name:
            hv = _hwmon_values(self.si.hwmon[hw_name])
            s.tctl = hv.get("Tctl")
            s.tdie = hv.get("Tdie")
            s.tccd = [hv[k] for k in sorted(hv) if k.startswith("Tccd")]
            s.vcore = hv.get("SVI2_Core")
            s.vsoc = hv.get("SVI2_SoC")
            s.icore = hv.get("SVI2_C_Core")
            s.isoc = hv.get("SVI2_C_SoC")
            pc, ps = hv.get("SVI2_P_Core"), hv.get("SVI2_P_SoC")
            if pc is not None:
                s.p_svi2 = pc + (ps or 0)
            s.sources["temp"] = hw_name
        if s.vcore is None and pm.get("vcore"):
            s.vcore = pm["vcore"]
            s.sources["volt"] = "SMU"
        if s.icore is None and pm.get("icore"):
            s.icore = pm["icore"]
        if s.tctl is None and pm.get("thm"):
            s.tctl = pm["thm"][0]
        s.package_w = tel.get("package_w")
        s.socket_w = pm.get("socket_w")
        for k in ("ppt", "tdc", "edc", "thm"):
            v = pm.get(k)
            if v:
                setattr(s, k, tuple(v))
        s.fclk, s.uclk, s.mclk = pm.get("fclk"), pm.get("uclk"), pm.get("mclk")
        s.sources["power"] = ("RAPL" if s.package_w is not None else "SMU" if s.socket_w is not None
                              else "SVI2 (zenpower)" if s.p_svi2 is not None else None)
        return s
