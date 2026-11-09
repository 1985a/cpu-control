"""Detección del procesador, topología, cpufreq, placa, BIOS, módulos y sensores (sin root)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from . import cpudb
from .paths import exists, glob, parse_cpulist, read, read_int


@dataclass
class Core:
    index: int                 # núcleo lógico 0..n-1
    threads: list[int]         # CPUs (hilos)
    core_id: int
    group: int                 # CCD/CCX (por L3 compartida)
    perf: int | None = None    # CPPC highest_perf / ranking
    rank: int | None = None    # 1 = mejor


@dataclass
class CpuFreq:
    driver: str | None = None
    pstate_status: str | None = None     # active | passive | guided
    governor: str | None = None
    governors: list[str] = field(default_factory=list)
    epp: str | None = None
    epps: list[str] = field(default_factory=list)
    min_khz: int | None = None
    max_khz: int | None = None
    hw_min_khz: int | None = None
    hw_max_khz: int | None = None
    boost: bool | None = None
    boost_path: str | None = None
    prefcore: bool | None = None


@dataclass
class Module:
    name: str
    loaded: bool
    installed: bool


@dataclass
class SystemInfo:
    model_name: str = "Desconocido"
    vendor: str = ""
    family: int = 0
    model: int = 0
    stepping: int = 0
    microcode: str = "—"
    flags: set = field(default_factory=set)
    platform: cpudb.Platform | None = None
    threads: int = 0
    cores: list[Core] = field(default_factory=list)
    groups: list[list[int]] = field(default_factory=list)   # índices de núcleo por CCD/CCX
    l1d_kb: int | None = None
    l1i_kb: int | None = None
    l2_kb: int | None = None
    l3_mb: float | None = None
    cpufreq: CpuFreq = field(default_factory=CpuFreq)
    board_vendor: str = "—"
    board_name: str = "—"
    bios_vendor: str = "—"
    bios_version: str = "—"
    bios_date: str = "—"
    kernel: str = "—"
    cmdline: str = ""
    modules: dict[str, Module] = field(default_factory=dict)
    hwmon: dict[str, str] = field(default_factory=dict)       # nombre → ruta
    blacklisted: set = field(default_factory=set)
    autoload: set = field(default_factory=set)
    lockdown: str | None = None
    polkit_agent: str | None = None
    smu_codename: int | None = None

    @property
    def is_amd(self) -> bool:
        return self.vendor == "AuthenticAMD"

    @property
    def is_zen(self) -> bool:
        return self.is_amd and self.family >= 0x17

    @property
    def smt(self) -> bool:
        return any(len(c.threads) > 1 for c in self.cores)

    @property
    def group_label(self) -> str:
        if self.platform and self.platform.generation in ("Zen", "Zen+", "Zen 2"):
            return "CCX"
        return "CCD"


def _cpuinfo() -> dict:
    text = read("/proc/cpuinfo", "") or ""
    first = text.split("\n\n")[0]
    info = {}
    for line in first.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    return info


def _size_kb(s: str | None) -> int | None:
    if not s:
        return None
    m = re.match(r"(\d+)\s*([KMG]?)", s)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return n * {"": 1, "K": 1, "M": 1024, "G": 1024 * 1024}[unit]


def _topology(si: SystemInfo) -> None:
    cpus = []
    for path in glob("/sys/devices/system/cpu/cpu[0-9]*"):
        n = int(path.rsplit("cpu", 1)[1])
        if read(f"{path}/online", "1") == "0":
            continue
        cpus.append(n)
    si.threads = len(cpus)
    seen: dict[tuple, Core] = {}
    l3_groups: dict[str, int] = {}
    for n in sorted(cpus):
        base = f"/sys/devices/system/cpu/cpu{n}"
        sib = read(f"{base}/topology/core_cpus_list") or read(f"{base}/topology/thread_siblings_list") or str(n)
        key = tuple(parse_cpulist(sib))
        if key in seen:
            continue
        l3 = read(f"{base}/cache/index3/shared_cpu_list", "0")
        g = l3_groups.setdefault(l3, len(l3_groups))
        core = Core(index=len(seen), threads=list(key), core_id=read_int(f"{base}/topology/core_id", n), group=g)
        perf = (read_int(f"{base}/cpufreq/amd_pstate_prefcore_ranking")
                or read_int(f"{base}/cpufreq/amd_pstate_highest_perf")
                or read_int(f"{base}/acpi_cppc/highest_perf"))
        core.perf = perf
        seen[key] = core
    si.cores = list(seen.values())
    groups: dict[int, list[int]] = {}
    for c in si.cores:
        groups.setdefault(c.group, []).append(c.index)
    si.groups = [groups[k] for k in sorted(groups)]
    perfs = [c.perf for c in si.cores if c.perf]
    if perfs and len(set(perfs)) > 1:
        ordered = sorted(si.cores, key=lambda c: -(c.perf or 0))
        for r, c in enumerate(ordered, 1):
            c.rank = r
    # Cachés
    for idx in glob("/sys/devices/system/cpu/cpu0/cache/index*"):
        level = read_int(f"{idx}/level")
        ctype = read(f"{idx}/type")
        size = _size_kb(read(f"{idx}/size"))
        if size is None:
            continue
        if level == 1 and ctype == "Data":
            si.l1d_kb = size
        elif level == 1 and ctype == "Instruction":
            si.l1i_kb = size
        elif level == 2:
            si.l2_kb = size * len(si.cores)
        elif level == 3:
            si.l3_mb = size * len(si.groups) / 1024


def _cpufreq(si: SystemInfo) -> None:
    f = CpuFreq()
    pol = "/sys/devices/system/cpu/cpufreq/policy0"
    if not exists(pol):
        pol = "/sys/devices/system/cpu/cpu0/cpufreq"
    f.driver = read(f"{pol}/scaling_driver")
    f.pstate_status = read("/sys/devices/system/cpu/amd_pstate/status")
    f.governor = read(f"{pol}/scaling_governor")
    f.governors = (read(f"{pol}/scaling_available_governors", "") or "").split()
    f.epp = read(f"{pol}/energy_performance_preference")
    f.epps = (read(f"{pol}/energy_performance_available_preferences", "") or "").split()
    f.min_khz = read_int(f"{pol}/scaling_min_freq")
    f.max_khz = read_int(f"{pol}/scaling_max_freq")
    f.hw_min_khz = read_int(f"{pol}/cpuinfo_min_freq")
    f.hw_max_khz = read_int(f"{pol}/cpuinfo_max_freq")
    for path in ("/sys/devices/system/cpu/cpufreq/boost", f"{pol}/boost"):
        v = read(path)
        if v is not None:
            f.boost, f.boost_path = v == "1", path
            break
    pc = read("/sys/devices/system/cpu/amd_pstate/prefcore")
    f.prefcore = None if pc is None else pc == "enabled"
    si.cpufreq = f


def _modules(si: SystemInfo) -> None:
    rel = si.kernel
    for name in ("k10temp", "zenpower", "ryzen_smu", "msr"):
        loaded = exists(f"/sys/module/{name}")
        installed = loaded or bool(
            glob(f"/usr/lib/modules/{rel}/updates/**/{name}.ko*")
            + glob(f"/usr/lib/modules/{rel}/extra/**/{name}.ko*")
            + glob(f"/usr/lib/modules/{rel}/kernel/drivers/hwmon/{name}.ko*")
            + glob(f"/usr/lib/modules/{rel}/updates/{name}.ko*")
            + glob(f"/usr/lib/modules/{rel}/kernel/arch/x86/kernel/{name}.ko*"))
        if name == "msr" and exists("/dev/cpu/0/msr"):
            loaded = installed = True
        si.modules[name] = Module(name, loaded, installed)
    for conf in glob("/etc/modprobe.d/*.conf") + glob("/usr/lib/modprobe.d/*.conf"):
        for line in (read(conf, "") or "").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "blacklist":
                si.blacklisted.add(parts[1])
    for conf in glob("/etc/modules-load.d/*.conf") + glob("/usr/lib/modules-load.d/*.conf"):
        for raw in (read(conf, "") or "").splitlines():
            line = raw.split("#")[0].strip()
            if line:
                si.autoload.add(line)
    if "module_blacklist" in si.cmdline:
        m = re.search(r"module_blacklist=(\S+)", si.cmdline)
        if m:
            si.blacklisted.update(m.group(1).split(","))


def _hwmon(si: SystemInfo) -> None:
    for path in glob("/sys/class/hwmon/hwmon*"):
        name = read(f"{path}/name")
        if name:
            si.hwmon.setdefault(name, path)


AGENTS = ("polkit-gnome-au", "polkit-kde-auth", "lxpolkit", "polkit-mate-aut", "mate-polkit", "xfce-polkit",
          "hyprpolkitagent", "soteria", "gnome-shell", "polkit-agent-he", "lxqt-policykit", "cosmic-osd",
          "ukui-polkit", "budgie-polkit", "pantheon-agent-", "polkit-efl-aut", "deepin-polkit")


def _polkit_agent() -> str | None:
    for comm_path in glob("/proc/[0-9]*/comm"):
        comm = read(comm_path, "") or ""
        for a in AGENTS:
            if comm.startswith(a):
                return comm
        if "polkit" in comm and comm not in ("polkitd", "polkit"):
            return comm
    return None


def detect() -> SystemInfo:
    si = SystemInfo()
    info = _cpuinfo()
    si.vendor = info.get("vendor_id", "")
    si.model_name = re.sub(r"\s+", " ", info.get("model name", "Desconocido")).replace(" Processor", "").strip()
    try:
        si.family = int(info.get("cpu family", "0"))
        si.model = int(info.get("model", "0"))
        si.stepping = int(info.get("stepping", "0"))
    except ValueError:
        pass
    si.microcode = info.get("microcode", "—")
    si.flags = set(info.get("flags", "").split())
    si.kernel = read("/proc/sys/kernel/osrelease") or os.uname().release
    si.cmdline = read("/proc/cmdline", "") or ""
    si.smu_codename = read_int("/sys/kernel/ryzen_smu_drv/codename")
    si.platform = cpudb.detect(si.family, si.model, si.model_name, si.smu_codename) if si.is_zen else None
    _topology(si)
    _cpufreq(si)
    _modules(si)
    _hwmon(si)
    for k in ("board_vendor", "board_name", "bios_vendor", "bios_version", "bios_date"):
        setattr(si, k, read(f"/sys/class/dmi/id/{k}", "—") or "—")
    lock = read("/sys/kernel/security/lockdown")
    if lock:
        m = re.search(r"\[(\w+)\]", lock)
        si.lockdown = m.group(1) if m else None
    si.polkit_agent = _polkit_agent()
    return si


def chipset(board_name: str) -> str | None:
    m = re.search(r"(?<![A-Z0-9])([ABX][2-9][0-9]0)", board_name.upper())
    return m.group(1) if m else None
