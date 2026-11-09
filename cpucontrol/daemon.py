"""Servicio de sistema (root): detecta el hardware, publica telemetría y aplica perfiles.

Archivos:
  /run/cpu-control/status.json     plataforma, funciones disponibles, límites, último resultado
  /run/cpu-control/telemetry.json  RAPL + tabla PM, cada segundo
  /run/cpu-control/stock.json      valores de fábrica capturados en el primer arranque de este boot
  /run/cpu-control/session.json    perfil aplicado solo hasta reiniciar
  /etc/cpu-control/active.json     perfil que se aplica en cada arranque
SIGHUP: vuelve a leer la configuración y aplica (lo usa el helper y el gancho de suspensión).
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time

from . import __version__, cpudb, cpufreq, presets, system
from .paths import (BOOT_FILE, RUN_DIR, SESSION_FILE, SETTINGS_FILE, STATUS_FILE, STOCK_FILE, TELEMETRY_FILE,
                    TOUCHED_FILE, glob, read, read_int)
from .profile import Profile, ValidationError, validate
from .smu import Smu, SmuError, build_core_map, decode_pm

INTERVAL = 1.0


def log(msg: str) -> None:
    print(msg, flush=True)


def _load(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path: str, data, mode=0o644) -> None:
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


class Rapl:
    def __init__(self):
        self.path = None
        for zone in glob("/sys/class/powercap/*rapl*:[0-9]"):
            if (read(f"{zone}/name") or "").startswith("package"):
                self.path = zone
                break
        self.max = read_int(f"{self.path}/max_energy_range_uj") if self.path else None
        self.prev = None

    def read_w(self) -> float | None:
        if not self.path:
            return None
        e = read_int(f"{self.path}/energy_uj")
        if e is None:
            return None
        now = time.monotonic()
        w = None
        if self.prev:
            de, dt = e - self.prev[0], now - self.prev[1]
            if de < 0 and self.max:
                de += self.max
            if dt > 0 and de >= 0:
                w = round(de / dt / 1e6, 2)
        self.prev = (e, now)
        return w


class Daemon:
    def __init__(self):
        self.reload_requested = False
        self.running = True
        self.si = system.detect()
        self.plat = self.si.platform
        self.prof = cpudb.SMU_PROFILES.get(self.plat.smu) if self.plat and self.plat.smu else None
        self.settings = _load(SETTINGS_FILE, {})
        try:
            self.smu = Smu()
            self.smu_ok = self.smu.test()
        except (SmuError, OSError) as e:
            log(f"SMU: {e}")
            self.smu, self.smu_ok = None, False
        self.cmap = build_core_map(self.smu if self.smu_ok else None, self.plat.fuses if self.plat else None,
                                   len(self.si.cores), self.si.groups)
        self.pm_version = None
        if self.smu_ok:
            self.pm_version, _ = self.smu.pm_table()
        self.apu = bool(self.prof and self.prof.style == "apu")
        self.rapl = Rapl()
        self.co_probe, self.co_current = self._probe_co()
        self.spec = presets.spec_for(self.si.model_name, self.plat.generation if self.plat else None,
                                     system.chipset(self.si.board_name))
        self.stock = self._stock()
        self.touched = _load(TOUCHED_FILE, {})
        self.active = {"name": None, "source": None, "results": [], "time": 0}
        log(f"CPU: {self.si.model_name} · {self.plat.codename if self.plat else '?'} · SMU "
            f"{'OK' if self.smu_ok else 'no disponible'} · tabla PM "
            f"{hex(self.pm_version) if self.pm_version else '—'} · mapa de núcleos "
            f"{'verificado' if self.cmap.verified else 'aproximado'}")

    # --- capacidades -----------------------------------------------------
    @property
    def experimental(self) -> bool:
        return bool(self.settings.get("experimental"))

    def features(self) -> dict:
        p, ok, exp = self.prof, self.smu_ok, self.experimental
        limits = bool(ok and p and p.ppt and (not p.limits_experimental or exp))
        co = bool(ok and p and p.co_range and p.co_set_all and (not p.co_experimental or exp))
        oc = bool(ok and p and p.oc_enable and p.oc_vid and p.oc_freq_all and (not p.oc_experimental or exp))
        f = self.si.cpufreq
        return {
            "limits": limits, "scalar": bool(limits and p.scalar), "co": co,
            "co_per_core": bool(co and p.co_set and self.cmap.verified), "oc": oc,
            "boost": bool(cpufreq.boost_paths()), "epp": bool(f.epps), "governor": bool(f.governors),
            "freq": bool(f.hw_min_khz and f.hw_max_khz),
            "pm": self.pm_version is not None and self._pm_known(), "rapl": self.rapl.path is not None,
        }

    def _pm_known(self) -> bool:
        from . import pmtables
        return self.pm_version in pmtables.TABLES

    def limits(self) -> dict:
        st = self.stock
        ppt, tdc, edc = st.get("ppt"), st.get("tdc"), st.get("edc")
        f = self.si.cpufreq
        hw = [f.hw_min_khz // 1000, f.hw_max_khz // 1000] if f.hw_min_khz and f.hw_max_khz else None
        co_r = self.prof.co_range if self.prof else 0
        cap = self.spec.cap
        if cap:
            # Tope según el modelo y la placa; nunca por debajo de lo que trae la BIOS
            ppt_r = [15, max(cap[0], round(ppt or 0))]
            tdc_r = [10, max(cap[1], round(tdc or 0))]
            edc_r = [10, max(cap[2], round(edc or 0))]
        else:
            ppt_r = [15, min(400, max(120, round((ppt or 150) * 1.5)))]
            tdc_r = [10, min(300, max(90, round((tdc or 100) * 1.5)))]
            edc_r = [10, min(400, max(140, round((edc or 150) * 1.5)))]
        return {
            "features": self.features(), "cores": len(self.si.cores),
            "ppt": ppt_r, "tdc": tdc_r, "edc": edc_r,
            "tctl": [60, self.spec.tjmax or 95],
            "co": [-co_r, co_r] if co_r else None,
            "oc_mhz": [2000, min(6000, (hw[1] if hw else 4500) + 300)],
            "oc_volts": [0.80, cpudb.max_manual_voltage(self.plat.generation if self.plat else None)],
            "epps": f.epps, "governors": f.governors, "freq_mhz": hw,
        }

    # --- captura de fábrica -----------------------------------------------
    def _probe_co(self):
        p = self.prof
        if not (self.smu_ok and p and p.co_range):
            return "n/a", None
        if not p.co_get_rsmu:
            return "sin comando de lectura", None
        values = []
        for logical in range(len(self.si.cores)):
            ccd, ccx, core = self.cmap.slots[logical]
            r = self.smu.send("rsmu", p.co_get_rsmu, cpudb.core_mask(self.si.family, ccd, ccx, core))
            if not r.ok:
                return f"el SMU respondió «{r.text}»", None
            values.append(cpudb.decode_co(r.args[0]))
            if not self.cmap.verified:
                break   # sin mapa fiable solo leemos el primero como prueba
        return "ok", values if self.cmap.verified else None

    def _stock(self) -> dict:
        st = _load(STOCK_FILE, None)
        if st and st.get("boot_id") == read("/proc/sys/kernel/random/boot_id"):
            return st
        st = {"boot_id": read("/proc/sys/kernel/random/boot_id"), "source": None,
              "cpufreq": cpufreq.capture(), "co": self.co_current}
        pm = self._pm()
        if pm:
            st["ppt"], st["tdc"], st["edc"], st["tctl"] = (pm["ppt"][1], pm["tdc"][1], pm["edc"][1], pm["thm"][1])
            if self.apu:
                st["ppt_slow"], st["stapm"] = pm.get("ppt_slow_limit"), pm.get("stapm_limit")
            st["source"] = "tabla PM"
        elif self.spec.stock:
            st["ppt"], st["tdc"], st["edc"] = self.spec.stock
            st["tctl"] = self.spec.tjmax
            st["source"] = "especificación AMD"
        _save(STOCK_FILE, st)
        return st

    def _pm(self) -> dict | None:
        if not (self.smu_ok and self.pm_version):
            return None
        ver, raw = self.smu.pm_table()
        return decode_pm(ver, raw, self.cmap, self.apu)

    # --- aplicar ------------------------------------------------------------
    def _limit(self, results, key, label, cmds, value, unit_scale=1000):
        r = self.smu.send_pair(cmds, self.prof.style, round(value * unit_scale))
        results.append({"item": label, "ok": r.ok, "msg": "aplicado" if r.ok else f"el SMU respondió «{r.text}»"})
        return r.ok

    def _co_all(self, value: int) -> tuple[bool, str]:
        p = self.prof
        for mb in ("mp1", "rsmu"):
            if p.co_set_all.get(mb):
                r = self.smu.send(mb, p.co_set_all[mb], value & 0xFFFF)
                if r.ok:
                    return True, "aplicado"
        return False, f"el SMU respondió «{r.text}»"

    def _co_core(self, logical: int, value: int) -> tuple[bool, str]:
        p = self.prof
        ccd, ccx, core = self.cmap.slots[logical]
        arg = cpudb.co_arg(cpudb.core_mask(self.si.family, ccd, ccx, core), value)
        for mb in ("mp1", "rsmu"):
            if p.co_set.get(mb):
                r = self.smu.send(mb, p.co_set[mb], arg)
                if r.ok:
                    return True, "aplicado"
        return False, f"el SMU respondió «{r.text}»"

    def apply(self, prof: Profile) -> list[dict]:
        if prof.mode == "stock":
            return self.restore()
        results: list[dict] = []
        st, t = self.stock, self.touched
        sc = st.get("cpufreq", {})

        # cpufreq: lo que el perfil no indica vuelve a fábrica si lo habíamos cambiado
        args = {}
        for k in ("boost", "epp", "governor", "min_mhz", "max_mhz"):
            v = getattr(prof, k)
            if v is None and t.get(f"cf_{k}"):
                v = sc.get(k)
            if v is not None:
                args[k] = v
                t[f"cf_{k}"] = getattr(prof, k) is not None
        results += cpufreq.apply(**args)

        feats = self.features()
        if self.smu_ok and self.prof:
            p = self.prof
            if prof.mode == "manual" and feats["oc"]:
                # Zen/Zen+ y Raven/Picasso usan el mismo comando con argumento 1 = activar
                r = self.smu.send_pair(p.oc_enable, p.style, 1 if p.key in ("zen1", "zenp", "apu0", "apu0p") else 0)
                ok = r.ok
                if ok:
                    t["oc"] = True
                    r2 = self.smu.send_pair(p.oc_freq_all, p.style, int(prof.oc_mhz) & 0xFFFFF)
                    vid = cpudb.voltage_to_vid(float(prof.oc_volts), p.svi)
                    r3 = self.smu.send_pair(p.oc_vid, p.style, vid)
                    ok = r2.ok and r3.ok
                    msg = (f"{prof.oc_mhz} MHz a {cpudb.vid_to_voltage(vid, p.svi):.3f} V" if ok
                           else f"frecuencia: {r2.text}; voltaje: {r3.text}")
                else:
                    msg = f"el SMU rechazó el modo OC («{r.text}»): la placa o la BIOS lo bloquean"
                results.append({"item": "OC manual", "ok": ok, "msg": msg})
            elif t.get("oc"):
                r = self.smu.send_pair(p.oc_disable, p.style, 0)
                results.append({"item": "Salir del OC manual", "ok": r.ok,
                                "msg": "boost automático restaurado" if r.ok else f"«{r.text}»; reinicia para salir"})
                t["oc"] = not r.ok

            if feats["limits"] and prof.mode != "manual":
                for key, label, cmds in (("ppt", "PPT", p.ppt), ("tdc", "TDC", p.tdc), ("edc", "EDC", p.edc)):
                    v = getattr(prof, key)
                    if v is None and t.get(key):
                        v = st.get(key)
                    if v is None or not cmds:
                        continue
                    if self._limit(results, key, label, cmds, v):
                        t[key] = getattr(prof, key) is not None
                    if key == "ppt" and self.apu:
                        for k2, cmds2, lab in (("ppt_slow", p.ppt_slow, "PPT lento"), ("stapm", p.ppt_stapm, "STAPM")):
                            v2 = prof.ppt if prof.ppt is not None else st.get(k2)
                            if v2 and cmds2:
                                self._limit(results, k2, lab, cmds2, v2)
                v = prof.tctl if prof.tctl is not None else (st.get("tctl") if t.get("tctl") else None)
                if (v is not None and p.tctl
                        and self._limit(results, "tctl", "Límite de temperatura", p.tctl, v, unit_scale=1)):
                    t["tctl"] = prof.tctl is not None
                v = prof.scalar if prof.scalar is not None else (1 if t.get("scalar") else None)
                if (v is not None and p.scalar
                        and self._limit(results, "scalar", "Escalar PBO", p.scalar, v, unit_scale=100)):
                    t["scalar"] = prof.scalar is not None

            if feats["co"] and prof.mode != "manual":
                if prof.co_mode == "all":
                    ok, msg = self._co_all(prof.co_all)
                    results.append({"item": f"Curve Optimizer (todos {prof.co_all:+d})", "ok": ok, "msg": msg})
                    t["co"] = t.get("co") or ok
                elif prof.co_mode == "per_core" and feats["co_per_core"]:
                    fails = []
                    for i, v in enumerate(prof.co_cores):
                        ok, msg = self._co_core(i, v)
                        if not ok:
                            fails.append((i, msg))
                    if not fails:
                        msg = ", ".join(f"{v:+d}" for v in prof.co_cores)
                    elif len(fails) == len(prof.co_cores):
                        msg = (f"{fails[0][1]} en todos los núcleos: tu BIOS o placa no permite cambiar el "
                               "Curve Optimizer desde el sistema")
                    else:
                        msg = "; ".join(f"núcleo {i}: {m}" for i, m in fails)
                    results.append({"item": "Curve Optimizer por núcleo", "ok": not fails, "msg": msg})
                    t["co"] = t.get("co") or len(fails) < len(prof.co_cores)
                elif t.get("co"):
                    results += self._restore_co()
                    t["co"] = False
        elif prof.mode in ("manual", "eco", "pbo") and any(
                getattr(prof, k) is not None for k in ("ppt", "tdc", "edc", "tctl", "oc_mhz")):
            results.append({"item": "Ajustes del SMU", "ok": False,
                            "msg": "ryzen_smu no está disponible: solo se aplicó cpufreq"})
        self.touched = t
        _save(TOUCHED_FILE, t)
        if prof.co_mode != "off" or prof.mode == "manual":
            self.co_probe, self.co_current = self._probe_co()
        return results

    def _restore_co(self) -> list[dict]:
        stock = self.stock.get("co")
        if stock and self.cmap.verified and self.prof.co_set:
            fails = [f"núcleo {i}: {m}" for i, v in enumerate(stock) for ok, m in [self._co_core(i, v)] if not ok]
            return [{"item": "Curve Optimizer de la BIOS", "ok": not fails,
                     "msg": "; ".join(fails) or ", ".join(f"{v:+d}" for v in stock)}]
        ok, msg = self._co_all(0)
        return [{"item": "Curve Optimizer a 0", "ok": ok,
                 "msg": msg if ok else msg + " (reinicia para volver a lo de la BIOS)"}]

    def restore(self) -> list[dict]:
        results: list[dict] = []
        t, st = self.touched, self.stock
        sc = st.get("cpufreq", {})
        args = {k: sc.get(k) for k in ("boost", "epp", "governor", "min_mhz", "max_mhz")
                if t.get(f"cf_{k}") and sc.get(k) is not None}
        if args:
            results += cpufreq.apply(**args)
        if self.smu_ok and self.prof:
            p = self.prof
            if t.get("oc"):
                r = self.smu.send_pair(p.oc_disable, p.style, 0)
                results.append({"item": "OC manual desactivado", "ok": r.ok, "msg": r.text})
            for key, label, cmds in (("ppt", "PPT", p.ppt), ("tdc", "TDC", p.tdc), ("edc", "EDC", p.edc)):
                if t.get(key):
                    if st.get(key):
                        self._limit(results, key, label, cmds, st[key])
                        if key == "ppt" and self.apu:
                            for k2, cmds2, lab in (("ppt_slow", p.ppt_slow, "PPT lento"),
                                                   ("stapm", p.ppt_stapm, "STAPM")):
                                if st.get(k2) and cmds2:
                                    self._limit(results, k2, lab, cmds2, st[k2])
                    else:
                        results.append({"item": label, "ok": False,
                                         "msg": "valor de fábrica desconocido: reinicia para restaurarlo"})
            if t.get("tctl") and st.get("tctl") and p.tctl:
                self._limit(results, "tctl", "Límite de temperatura", p.tctl, st["tctl"], unit_scale=1)
            if t.get("scalar") and p.scalar:
                self._limit(results, "scalar", "Escalar PBO", p.scalar, 1, unit_scale=100)
            if t.get("co"):
                results += self._restore_co()
        if not results:
            results.append({"item": "Valores de fábrica", "ok": True, "msg": "no había cambios que deshacer"})
        self.touched = {}
        _save(TOUCHED_FILE, {})
        return results

    def apply_configured(self, reason: str) -> None:
        self.settings = _load(SETTINGS_FILE, {})
        src, data = None, None
        if os.path.exists(SESSION_FILE):
            src, data = "session", _load(SESSION_FILE, None)
        elif os.path.exists(BOOT_FILE):
            src, data = "boot", _load(BOOT_FILE, None)
        if data is None:
            if self.touched:
                log(f"[{reason}] sin perfil: restaurando valores de fábrica")
                self.active = {"name": "Fábrica", "source": None, "results": self.restore(), "time": time.time()}
            else:
                self.active = {"name": None, "source": None, "results": [], "time": time.time()}
            return
        try:
            prof = validate(Profile.from_dict(data.get("profile", {})), self.limits())
        except (ValidationError, TypeError, KeyError) as e:
            log(f"[{reason}] perfil inválido: {e}")
            self.active = {"name": None, "source": src, "time": time.time(),
                           "results": [{"item": "Perfil", "ok": False, "msg": f"inválido: {e}"}]}
            return
        log(f"[{reason}] aplicando «{prof.name}» ({src})")
        results = self.apply(prof)
        for r in results:
            log(f"  {'✓' if r['ok'] else '✗'} {r['item']}: {r['msg']}")
        self.active = {"name": prof.name, "mode": prof.mode, "source": src, "results": results,
                       "time": time.time()}

    # --- estado y bucle -----------------------------------------------------
    def status(self) -> dict:
        return {
            "time": time.time(), "daemon_version": __version__, "pid": os.getpid(),
            "codename": self.plat.codename if self.plat else None,
            "smu": {"loaded": self.smu is not None, "test_ok": self.smu_ok,
                    "version": self.smu.version if self.smu else None,
                    "drv_version": self.smu.drv_version if self.smu else None,
                    "codename": self.smu.codename if self.smu else None,
                    "pm_version": self.pm_version, "pm_known": self._pm_known() if self.pm_version else False,
                    "profile": self.prof.key if self.prof else None},
            "coremap": {"verified": self.cmap.verified, "note": self.cmap.note, "slots": self.cmap.slots},
            "co_probe": self.co_probe, "co_current": self.co_current,
            "stock": {k: v for k, v in self.stock.items() if k != "boot_id"},
            "limits": self.limits(), "experimental": self.experimental, "active": self.active,
            "spec": self.spec.to_dict(),
            "touched": self.touched,
        }

    def telemetry(self) -> dict:
        return {"time": time.time(), "package_w": self.rapl.read_w(), "pm": self._pm()}

    def run(self) -> None:
        signal.signal(signal.SIGHUP, self._hup)
        signal.signal(signal.SIGTERM, self._term)
        signal.signal(signal.SIGINT, self._term)
        os.makedirs(RUN_DIR, mode=0o755, exist_ok=True)
        self.apply_configured("inicio")
        _save(STATUS_FILE, self.status())
        while self.running:
            if self.reload_requested:
                self.reload_requested = False
                self.apply_configured("recarga")
                _save(STATUS_FILE, self.status())
            try:
                _save(TELEMETRY_FILE, self.telemetry())
            except OSError as e:
                log(f"telemetría: {e}")
            time.sleep(INTERVAL)

    def _hup(self, *_):
        self.reload_requested = True

    def _term(self, *_):
        self.running = False


def main() -> None:
    if os.geteuid() != 0:
        print("cpu-control-daemon debe ejecutarse como root (systemd).", file=sys.stderr)
        sys.exit(1)
    Daemon().run()


if __name__ == "__main__":
    main()
