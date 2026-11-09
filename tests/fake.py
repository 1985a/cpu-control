"""Equipos simulados para pruebas y capturas: genera un árbol /sys y /proc falso y un SMU falso.

Uso:  python3 tests/fake.py <carpeta> <5500|5950x|7800x3d> [--smu] [--live]
"""
from __future__ import annotations

import math
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

PRESETS = {
    "5500": {"name": "AMD Ryzen 5 5500 6-Core Processor", "family": 25, "model": 0x50, "cores": 6, "smt": 2, "groups": 1,
                 "max_khz": 4267000, "min_khz": 550000, "l3_kb": 16384, "l2_kb": 512, "hw": "zenpower",
                 "board": ("Micro-Star International Co., Ltd.", "B350 GAMING PLUS (MS-7A34)"),
                 "bios": ("American Megatrends International, LLC.", "1.N0", "01/12/2023"),
                 "smu_codename": 14, "pm_version": 0x400005, "fuses": {0x5D448: (0b01001000 << 11)},
                 "stock": {"ppt": 88, "tdc": 60, "edc": 90, "thm": 90}, "co": [-5, 0, -5, -10, 0, -5],
                 "perf": [166, 196, 176, 186, 171, 161]},
    "5950x": {"name": "AMD Ryzen 9 5950X 16-Core Processor", "family": 25, "model": 0x21, "cores": 16, "smt": 2, "groups": 2,
                  "max_khz": 5083000, "min_khz": 550000, "l3_kb": 32768, "l2_kb": 512, "hw": "k10temp",
                  "board": ("ASUSTeK COMPUTER INC.", "ROG STRIX X570-E GAMING"),
                  "bios": ("American Megatrends Inc.", "5003", "10/07/2023"),
                  "smu_codename": 12, "pm_version": 0x380805,
                  "fuses": {0x5D218: 0x00400000, 0x5D21C: 0x3F, 0x30081D98: 0, 0x30081D98 + (1 << 25): 0},
                  "stock": {"ppt": 142, "tdc": 95, "edc": 140, "thm": 90}, "co": [0] * 16,
                  "perf": [226, 216, 231, 211, 221, 206, 201, 196, 191, 186, 181, 176, 171, 166, 161, 156]},
    "7800x3d": {"name": "AMD Ryzen 7 7800X3D 8-Core Processor", "family": 25, "model": 0x61, "cores": 8, "smt": 2, "groups": 1,
                    "max_khz": 5050000, "min_khz": 545000, "l3_kb": 98304, "l2_kb": 1024, "hw": "k10temp",
                    "board": ("Gigabyte Technology Co., Ltd.", "A620M GAMING X"), "bios": ("American Megatrends", "F3", "2024"),
                    "smu_codename": 20, "pm_version": 0x540104, "fuses": {}, "stock": {"ppt": 162, "tdc": 120, "edc": 180, "thm": 89},
                    "co": [0] * 8, "perf": []},
}


def w(root, path, value):
    full = root + path
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(str(value) + ("\n" if not str(value).endswith("\n") else ""))


def build(root: str, key: str, smu: bool = False) -> dict:
    p = PRESETS[key]
    n_threads = p["cores"] * p["smt"]
    per_group = p["cores"] // p["groups"]
    w(root, "/proc/cpuinfo", f"processor\t: 0\nvendor_id\t: AuthenticAMD\ncpu family\t: {p['family']}\n"
      f"model\t\t: {p['model']}\nmodel name\t: {p['name']}\nstepping\t: 0\nmicrocode\t: 0xa50000c\n"
      "flags\t\t: fpu sse avx2 cppc\n\n")
    w(root, "/proc/sys/kernel/osrelease", "6.17.7-arch1-1")
    w(root, "/proc/sys/kernel/random/boot_id", "mock-boot-1")
    w(root, "/proc/cmdline", "root=/dev/nvme0n1p2 rw quiet")
    for t in range(n_threads):
        core = t % p["cores"]
        base = f"/sys/devices/system/cpu/cpu{t}"
        sibs = ",".join(str(core + k * p["cores"]) for k in range(p["smt"]))
        w(root, f"{base}/topology/core_cpus_list", sibs)
        w(root, f"{base}/topology/core_id", core)
        g = core // per_group
        lo, hi = g * per_group, g * per_group + per_group - 1
        w(root, f"{base}/cache/index3/shared_cpu_list", f"{lo}-{hi},{lo + p['cores']}-{hi + p['cores']}")
        if t == 0:
            for i, (lvl, typ, size) in enumerate([(1, "Data", "32K"), (1, "Instruction", "32K"),
                                                  (2, "Unified", f"{p['l2_kb']}K"), (3, "Unified", f"{p['l3_kb']}K")]):
                w(root, f"{base}/cache/index{i}/level", lvl)
                w(root, f"{base}/cache/index{i}/type", typ)
                w(root, f"{base}/cache/index{i}/size", size)
        if p["perf"]:
            w(root, f"{base}/acpi_cppc/highest_perf", p["perf"][core])
        w(root, f"{base}/cpufreq/scaling_cur_freq", 3600000)
        pol = f"/sys/devices/system/cpu/cpufreq/policy{t}"
        w(root, f"{pol}/scaling_driver", "amd-pstate-epp")
        w(root, f"{pol}/scaling_governor", "powersave")
        w(root, f"{pol}/scaling_available_governors", "performance powersave")
        w(root, f"{pol}/energy_performance_preference", "balance_performance")
        w(root, f"{pol}/energy_performance_available_preferences",
          "default performance balance_performance balance_power power")
        w(root, f"{pol}/scaling_min_freq", p["min_khz"])
        w(root, f"{pol}/scaling_max_freq", p["max_khz"])
        w(root, f"{pol}/cpuinfo_min_freq", p["min_khz"])
        w(root, f"{pol}/cpuinfo_max_freq", p["max_khz"])
    w(root, "/sys/devices/system/cpu/cpufreq/boost", 1)
    w(root, "/sys/devices/system/cpu/amd_pstate/status", "active")
    w(root, "/sys/class/dmi/id/board_vendor", p["board"][0])
    w(root, "/sys/class/dmi/id/board_name", p["board"][1])
    w(root, "/sys/class/dmi/id/bios_vendor", p["bios"][0])
    w(root, "/sys/class/dmi/id/bios_version", p["bios"][1])
    w(root, "/sys/class/dmi/id/bios_date", p["bios"][2])
    hw = "/sys/class/hwmon/hwmon2"
    w(root, f"{hw}/name", p["hw"])
    os.makedirs(root + f"/sys/module/{p['hw']}", exist_ok=True)
    labels = [("temp1", "Tctl"), ("temp2", "Tdie")]
    if p["hw"] == "zenpower":
        labels += [("in1", "SVI2_Core"), ("in2", "SVI2_SoC"), ("curr1", "SVI2_C_Core"), ("curr2", "SVI2_C_SoC"),
                   ("power1", "SVI2_P_Core"), ("power2", "SVI2_P_SoC")]
        w(root, "/etc/modprobe.d/k10temp.conf", "blacklist k10temp")
    else:
        labels += [(f"temp{3 + i}", f"Tccd{1 + i}") for i in range(p["groups"])]
    for f, lab in labels:
        w(root, f"{hw}/{f}_label", lab)
        w(root, f"{hw}/{f}_input", 0)
    os.makedirs(root + "/proc/1", exist_ok=True)
    w(root, "/proc/1234/comm", "polkit-gnome-au")
    w(root, "/sys/class/powercap/intel-rapl:0/name", "package-0")
    w(root, "/sys/class/powercap/intel-rapl:0/energy_uj", 0)
    w(root, "/sys/class/powercap/intel-rapl:0/max_energy_range_uj", 65532610987)
    if smu:
        d = "/sys/kernel/ryzen_smu_drv"
        w(root, f"{d}/codename", p["smu_codename"])
        w(root, f"{d}/version", "64.62.0" if key == "5500" else "56.58.0")
        w(root, f"{d}/drv_version", "0.1.7")
        os.makedirs(root + "/sys/module/ryzen_smu", exist_ok=True)
        w(root, "/etc/modules-load.d/ryzen_smu.conf", "ryzen_smu")
    stat_tick(root, p, 0)
    return p


def stat_tick(root, p, t):
    """Escribe /proc/stat, frecuencias y sensores con una carga que varía en el tiempo."""
    n = p["cores"] * p["smt"]
    load = max(0.03, min(1.0, 0.5 + 0.48 * math.sin(t / 7)))
    lines = ["cpu  0 0 0 0 0 0 0 0 0 0"]
    for c in range(n):
        busy = int(t * 100 * load * (0.7 + 0.3 * math.sin(c + t / 3)) + 1)
        idle = int(t * 100) - busy + 10000
        lines.append(f"cpu{c} {busy} 0 0 {max(idle, 0)} 0 0 0 0 0 0")
    w(root, "/proc/stat", "\n".join(lines))
    for c in range(n):
        f = p["min_khz"] + (p["max_khz"] - p["min_khz"]) * load * (0.92 + 0.08 * math.sin(c))
        w(root, f"/sys/devices/system/cpu/cpu{c}/cpufreq/scaling_cur_freq", int(f))
    hw = "/sys/class/hwmon/hwmon2"
    temp = 38 + 40 * load
    w(root, f"{hw}/temp1_input", int(temp * 1000))
    w(root, f"{hw}/temp2_input", int(temp * 1000))
    if p["hw"] == "zenpower":
        w(root, f"{hw}/in1_input", int((0.95 + 0.33 * load) * 1000))
        w(root, f"{hw}/in2_input", 1087)
        w(root, f"{hw}/curr1_input", int(55000 * load + 3000))
        w(root, f"{hw}/curr2_input", 9000)
        w(root, f"{hw}/power1_input", int((65 * load + 4) * 1e6))
        w(root, f"{hw}/power2_input", int(9.5e6))
    else:
        for i in range(p["groups"]):
            w(root, f"{hw}/temp{3 + i}_input", int((temp + 3 - 4 * i) * 1000))
    return load


class FakeSmu:
    """Imita al módulo ryzen_smu: responde comandos y entrega una tabla PM coherente."""

    def __init__(self, key: str, reject: set | None = None):
        from cpucontrol import pmtables
        self.p = PRESETS[key]
        self.version = "64.62.0"
        self.drv_version = "0.1.7"
        self.codename = self.p["smu_codename"]
        self.log: list[tuple] = []
        self.reject = reject or set()
        self.co = list(self.p["co"])
        self.limits = dict(self.p["stock"])
        self.pmt = pmtables.TABLES.get(self.p["pm_version"])
        self.load = 0.5

    def has(self, mb):
        return True

    def send_pair(self, cmds, style, *args):
        from cpucontrol.smu import Smu
        return Smu.send_pair(self, cmds, style, *args)

    def send(self, mb, cmd, *args):
        from cpucontrol import cpudb
        from cpucontrol.smu import Result
        self.log.append((mb, cmd, args))
        if (mb, cmd) in self.reject or cmd in self.reject:
            return Result(0xFD, (0,) * 6)
        if cmd == cpudb.SMU_TEST_MSG:
            return Result(1, (args[0] + 1, 0, 0, 0, 0, 0))
        prof = cpudb.SMU_PROFILES["cezanne" if self.codename == 14 else "zen3" if self.codename == 12 else "zen4"]
        if mb == "rsmu" and cmd == prof.co_get_rsmu:
            core = (args[0] >> 20) & 0xF
            ccd = args[0] >> 28
            idx = self._logical(ccd, core)
            return Result(1, (self.co[idx] & 0xFFFFFFFF, 0, 0, 0, 0, 0))
        if cmd == prof.co_set.get(mb):
            core, ccd = (args[0] >> 20) & 0xF, args[0] >> 28
            from cpucontrol.cpudb import decode_co
            self.co[self._logical(ccd, core)] = decode_co(args[0] & 0xFFFF)
        if cmd == prof.co_set_all.get(mb):
            from cpucontrol.cpudb import decode_co
            self.co = [decode_co(args[0])] * len(self.co)
        for k in ("ppt", "tdc", "edc"):
            if cmd == getattr(prof, k).get(mb):
                self.limits[k] = args[0] / 1000
        return Result(1, (0,) * 6)

    def _logical(self, ccd, core):
        slots = []
        for phys in range(4):
            for s in range(8):
                slots.append((phys, s))
        enabled = [(c, s) for (c, s) in slots if self._enabled(c, s)]
        return enabled.index((ccd, core)) if (ccd, core) in enabled else 0

    def _enabled(self, ccd, slot):
        if self.codename == 14:
            dis = (self.p["fuses"][0x5D448] >> 11) & 0xFF
            return ccd == 0 and not dis & (1 << slot)
        per = self.p["cores"] // self.p["groups"]
        return ccd < self.p["groups"] and slot < per

    def smn_read(self, addr):
        return self.p["fuses"].get(addr, 0 if self.codename == 12 else None)

    def test(self):
        return True

    def pm_table(self):
        if not self.pmt:
            return self.p["pm_version"], b"\0" * 4096
        n = 1200
        vals = [0.0] * n
        s, a = self.pmt["scalar"], self.pmt["array"]
        L = self.load
        lim = self.limits

        def put(name, v):
            if name in s and s[name] < n:
                vals[s[name]] = v
        ppt_v = 20 + (lim["ppt"] - 22) * L
        for name, v in (("PPT_LIMIT", lim["ppt"]), ("PPT_VALUE", ppt_v), ("PPT_LIMIT_FAST", lim["ppt"]),
                        ("PPT_VALUE_FAST", ppt_v), ("STAPM_LIMIT", lim["ppt"]), ("TDC_LIMIT", lim["tdc"]),
                        ("TDC_VALUE", 8 + (lim["tdc"] - 12) * L), ("EDC_LIMIT", lim["edc"]),
                        ("EDC_VALUE", 15 + (lim["edc"] - 20) * L), ("THM_LIMIT", self.p["stock"]["thm"]),
                        ("THM_VALUE", 38 + 40 * L), ("SOCKET_POWER", ppt_v - 3), ("FCLK_FREQ", 1600.0),
                        ("UCLK_FREQ", 1600.0), ("MEMCLK_FREQ", 1600.0), ("CPU_TELEMETRY_VOLTAGE", 0.95 + 0.33 * L),
                        ("CPU_TELEMETRY_CURRENT", 5 + 50 * L), ("SOC_TELEMETRY_VOLTAGE", 1.087)):
            put(name, v)
        slots_total = 8 * self.p["groups"]
        k = 0
        for phys in range(slots_total):
            ccd, slot = divmod(phys, 8)
            if not self._enabled(ccd, slot):
                continue
            wave = 0.85 + 0.15 * math.sin(k + time.time() / 2)
            for name, v in (("CORE_FREQEFF", (0.6 + (self.p["max_khz"] / 1e6 - 0.6) * L) * wave),
                            ("CORE_POWER", (0.3 + 9 * L) * wave), ("CORE_TEMP", 36 + 38 * L * wave),
                            ("CORE_VOLTAGE", 0.9 + 0.35 * L), ("CORE_C0", 100 * L * wave)):
                idx = a.get(name)
                if idx and phys < len(idx) and idx[phys] < n:
                    vals[idx[phys]] = v
            k += 1
        return self.p["pm_version"], struct.pack(f"<{n}f", *vals)


def live(root: str, key: str, smu_obj: FakeSmu | None = None, daemon=None, stop=None):
    """Actualiza los archivos simulados cada 0.5 s (y la telemetría si hay daemon)."""
    p = PRESETS[key]
    t0 = time.time()
    energy = 0.0
    while not (stop and stop.is_set()):
        t = time.time() - t0 + 5
        load = stat_tick(root, p, t)
        energy += (20 + 70 * load) * 0.5 * 1e6
        w(root, "/sys/class/powercap/intel-rapl:0/energy_uj", int(energy))
        if smu_obj:
            smu_obj.load = load
        if daemon:
            from cpucontrol.daemon import _save
            from cpucontrol.paths import TELEMETRY_FILE
            _save(TELEMETRY_FILE, daemon.telemetry())
        time.sleep(0.5)


if __name__ == "__main__":
    root, key = sys.argv[1], sys.argv[2]
    build(root, key, "--smu" in sys.argv)
    print(f"Árbol simulado de {key} en {root}")
