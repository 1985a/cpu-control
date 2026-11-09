"""Pruebas de la lógica sin hardware: detección, diagnóstico, servicio y helper con un SMU falso.

Uso: python3 tests/test_logic.py   (crea árboles simulados en una carpeta temporal)
"""
import importlib
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)


def fresh(sysroot):
    os.environ["CPU_CONTROL_SYSROOT"] = sysroot
    for m in [m for m in sys.modules if m.startswith("cpucontrol")]:
        del sys.modules[m]
    return importlib.import_module("cpucontrol")


def run_case(key, smu=True, reject=None):
    import fake
    tmp = tempfile.mkdtemp(prefix=f"cpuc-{key}-")
    fake.build(tmp, key, smu=smu)
    fresh(tmp)
    from cpucontrol import diagnostics, smu as smumod, system
    from cpucontrol import daemon as dmod
    from cpucontrol.profile import Profile, ValidationError, preset, validate
    print(f"\n===== {key}  (ryzen_smu={'sí' if smu else 'no'}) =====")
    si = system.detect()
    print(f"CPU: {si.model_name} · {si.platform.codename} · {si.platform.generation} · "
          f"{len(si.cores)} núcleos / {si.threads} hilos · grupos {si.groups} · L3 {si.l3_mb} MB")
    print("Ranking:", [c.rank for c in si.cores], "· driver", si.cpufreq.driver, "· EPP", si.cpufreq.epp)
    print("Placa:", si.board_name, "· chipset", system.chipset(si.board_name), "· hwmon", list(si.hwmon))

    fs = fake.FakeSmu(key, reject=reject) if smu else None
    if smu:
        dmod.Smu = lambda: fs
    else:
        def nosmu():
            raise smumod.SmuError("ryzen_smu no está cargado")
        dmod.Smu = nosmu
    d = dmod.Daemon()
    st = d.status()
    feats = st["limits"]["features"]
    print("Funciones:", {k: v for k, v in feats.items() if v})
    print("Mapa de núcleos:", st["coremap"]["verified"], st["coremap"]["slots"][:8], "·", st["coremap"]["note"])
    print("CO leído:", st["co_current"], "· prueba:", st["co_probe"])
    print("Fábrica:", {k: st["stock"].get(k) for k in ("ppt", "tdc", "edc", "tctl", "source")})
    tel = d.telemetry()
    if tel["pm"]:
        pm = tel["pm"]
        print("Tabla PM: PPT", pm["ppt"], "TDC", pm["tdc"], "EDC", pm["edc"], "FCLK", pm["fclk"],
              "· frec. por núcleo", [round(x or 0) for x in pm.get("core_freq_eff", [])][:8])

    rep = diagnostics.build(si, st, service_running=True)
    print("Diagnóstico:")
    for c in rep.checks:
        print(f"  [{c.level:5}] {c.group:13} {c.title}")
    for c in rep.caps:
        print(f"  {'✔' if c.available else '✘'} {c.name}: {c.reason}")

    lim = st["limits"]
    # Perfiles recomendados: todos deben pasar la validación con los límites reales
    from cpucontrol import presets
    co_r = (lim.get("co") or [0, 0])[1]
    from cpucontrol import cpudb
    items = presets.build(d.spec, st["stock"], lim["features"], si.cores, co_r, si.cpufreq.epps,
                          oc_cap=cpudb.max_manual_voltage(si.platform.generation))
    print("Tope:", d.spec.cap, "·", d.spec.cap_reason, "· rango PPT", lim["ppt"])
    for pr in items:
        ok = all(lim["features"].get(n) for n in pr.needs)
        prof = Profile.from_dict({"name": pr.name, **pr.values})
        if ok:
            validate(prof, lim)
        print(f"  perfil {pr.name:24} {'✓' if ok else '—'} {pr.summary}"
              + (f" · CO {prof.co_cores or prof.co_all}" if prof.co_mode != 'off' else ""))
    # Validación
    bad = [Profile(ppt=9999), Profile(co_mode="per_core", co_cores=[-40] * len(si.cores)),
           Profile(mode="manual", oc_mhz=4200, oc_volts=1.6), Profile(epp="turbo"), Profile(min_mhz=5000, max_mhz=3000)]
    for b in bad:
        try:
            validate(b, lim)
            print("  ¡NO RECHAZADO!", b)
        except ValidationError as e:
            print("  rechaza:", e)

    if smu:
        stock = st["stock"]
        pbo = preset("pbo", lim, stock, len(si.cores))
        if feats.get("co_per_core"):
            pbo.co_cores = [-15, -10, -20, -15, -10, -20, -5, -5, -10, -10, -10, -10, -10, -10, -10, -10][:len(si.cores)]
        validate(pbo, lim)
        print("Aplicar PBO:", {k: getattr(pbo, k) for k in ("ppt", "tdc", "edc", "co_mode")})
        for r in d.apply(pbo):
            print(f"  {'✓' if r['ok'] else '✗'} {r['item']}: {r['msg']}")
        print("  SMU límites ahora:", fs.limits, "· CO:", fs.co)
        eco = preset("eco", lim, stock, len(si.cores))
        print("Aplicar Eco:", {k: getattr(eco, k) for k in ("ppt", "tdc", "edc", "epp")})
        for r in d.apply(eco):
            print(f"  {'✓' if r['ok'] else '✗'} {r['item']}: {r['msg']}")
        print("  SMU límites ahora:", fs.limits, "· CO:", fs.co)
        print("Restaurar fábrica:")
        for r in d.restore():
            print(f"  {'✓' if r['ok'] else '✗'} {r['item']}: {r['msg']}")
        print("  SMU límites ahora:", fs.limits, "· CO:", fs.co, "· touched", d.touched)
        assert fs.limits == fake.PRESETS[key]["stock"] | {"thm": fake.PRESETS[key]["stock"]["thm"]}, fs.limits
        if feats.get("co_per_core"):
            assert fs.co == fake.PRESETS[key]["co"], fs.co
        epp = open(tmp + "/sys/devices/system/cpu/cpufreq/policy0/energy_performance_preference").read().strip()
        assert epp == "balance_performance", epp
    else:
        p = Profile(name="Solo cpufreq", boost=False, epp="power")
        validate(p, lim)
        for r in d.apply(p):
            print(f"  {'✓' if r['ok'] else '✗'} {r['item']}: {r['msg']}")
        assert open(tmp + "/sys/devices/system/cpu/cpufreq/boost").read().strip() == "0"
        for r in d.restore():
            print(f"  {'✓' if r['ok'] else '✗'} {r['item']}: {r['msg']}")
        assert open(tmp + "/sys/devices/system/cpu/cpufreq/boost").read().strip() == "1"
    shutil.rmtree(tmp)
    return st


if __name__ == "__main__":
    run_case("5500", smu=False)
    run_case("5500", smu=True)
    run_case("5950x", smu=True)
    run_case("7800x3d", smu=True)
    print("\nRechazo del SMU (placa que bloquea Curve Optimizer):")
    run_case("5500", smu=True, reject={("mp1", 0x54), ("mp1", 0x55), ("rsmu", 0xC3)})
    print("\nTODAS LAS PRUEBAS TERMINARON")
