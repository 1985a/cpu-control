"""Diagnóstico: qué falta instalar, qué no soporta la placa/BIOS y qué funciones quedan activas."""
from __future__ import annotations

from dataclasses import dataclass, field

from .system import SystemInfo, chipset

OK, INFO, WARN, ERROR = "ok", "info", "warn", "error"


@dataclass
class Check:
    title: str
    level: str
    detail: str
    fix: str | None = None
    group: str = "Software"


@dataclass
class Capability:
    name: str
    available: bool
    reason: str
    partial: bool = False


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    caps: list[Capability] = field(default_factory=list)

    @property
    def worst(self) -> str:
        levels = [c.level for c in self.checks]
        for lv in (ERROR, WARN):
            if lv in levels:
                return lv
        return OK

    def cap(self, name: str) -> Capability | None:
        return next((c for c in self.caps if c.name == name), None)


BIOS_CPPC_PATH = ("En la BIOS: Settings › Advanced › AMD CBS › NBIO Common Options › SMU Common Options › "
                  "CPPC = Enabled y CPPC Preferred Cores = Enabled (la ruta exacta cambia según la marca).")


def build(si: SystemInfo, status: dict, service_running: bool) -> Report:
    r = Report()
    add = r.checks.append
    plat = si.platform
    gen = plat.generation if plat else "?"
    feats = (status.get("limits") or {}).get("features", {}) if service_running else {}
    smu_st = status.get("smu", {}) if service_running else {}
    zen4plus = gen.startswith(("Zen 4", "Zen 5"))

    # --- Procesador ---
    if not si.is_amd:
        add(Check("Procesador no compatible", ERROR, f"{si.model_name} no es un procesador AMD. "
                  "CPU Control solo funciona con Ryzen / Threadripper.", group="Procesador"))
    elif not si.is_zen:
        add(Check("Procesador AMD anterior a Ryzen", ERROR, "Se necesita un Ryzen (Zen o posterior).",
                  group="Procesador"))
    elif plat is None:
        add(Check("Generación no reconocida", WARN, f"Familia {si.family:#x}, modelo {si.model:#x}: solo "
                  "se mostrará el monitor básico.", group="Procesador"))
    else:
        add(Check(f"{si.model_name}", OK, f"{plat.codename} · {plat.generation} · {plat.socket} · {plat.process}",
                  group="Procesador"))
        if plat.kind in ("mobile", "hedt"):
            add(Check("Plataforma no probada", WARN, "Esta app está pensada para Ryzen de escritorio (AM4/AM5). "
                      "En portátiles y Threadripper el monitor funciona, pero los ajustes del SMU no están "
                      "validados.", group="Procesador"))

    # --- Placa y BIOS ---
    cs = chipset(si.board_name)
    add(Check(f"{si.board_vendor} {si.board_name}", INFO,
              f"BIOS {si.bios_version} ({si.bios_date}) · chipset {cs or 'desconocido'}", group="Placa y BIOS"))
    if cs:
        if cs.startswith("A"):
            add(Check(f"Chipset {cs}: overclock limitado", WARN,
                      "AMD no permite overclock en chipsets serie A. El OC manual y, en muchas BIOS, PBO y "
                      "Curve Optimizer quedan bloqueados. La app los intenta y te dice si el SMU los rechaza.",
                      group="Placa y BIOS"))
        if cs[1] == "3" and cs[0] in "ABX" and gen in ("Zen 3",):
            add(Check(f"Placa serie 300 con {gen}", INFO,
                      "Funciona gracias a la BIOS con soporte Zen 3 (AGESA ComboV2 1.2.0.x). PBO y Curve "
                      "Optimizer existen en el procesador, pero algunos fabricantes los ocultan o los bloquean "
                      "en estas placas. Si al aplicar el SMU responde «rechazado», actualiza a la última BIOS "
                      "de tu placa. El OC manual depende de los VRM: en B350 mantén voltajes y potencia "
                      "moderados.", group="Placa y BIOS"))
        if cs in ("B350", "A320", "B450", "A520") and gen == "Zen 3":
            add(Check("VRM de gama de entrada", INFO,
                      "En estas placas conviene no subir mucho PPT/TDC/EDC: los VRM se calientan. Para un "
                      "Ryzen de 65 W, PBO moderado (≈ 100 W PPT) y un Curve Optimizer negativo dan mejor "
                      "resultado que subir el voltaje.", group="Placa y BIOS"))

    # --- Servicio ---
    if service_running:
        add(Check("Servicio cpu-control activo", OK, "Publica telemetría y aplica los perfiles.", group="Servicio"))
    else:
        add(Check("Servicio cpu-control detenido", WARN,
                  "Sin el servicio no hay potencia RAPL, tabla PM, mapa de núcleos ni ajustes. "
                  "Puedes activarlo con el botón «Activar servicio» o con el comando:",
                  "sudo systemctl enable --now cpu-control.service", group="Servicio"))
    if si.polkit_agent:
        add(Check("Agente de autenticación (polkit)", OK, f"Detectado: {si.polkit_agent}", group="Servicio"))
    else:
        add(Check("Falta un agente de polkit", WARN,
                  "Sin agente no aparece la ventana de contraseña y no podrás aplicar cambios. En Sway agrega "
                  "a tu config:", "exec /usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1   "
                  "# paquete polkit-gnome", group="Servicio"))

    # --- Sensores ---
    mods = si.modules
    zp = mods.get("zenpower")
    if "zenpower" in si.hwmon:
        add(Check("zenpower3 activo", OK, "Temperatura, voltaje, corriente y potencia SVI2.", group="Sensores"))
    elif zen4plus:
        if "k10temp" in si.hwmon:
            add(Check("k10temp activo", OK, "Temperatura Tctl/Tccd. (zenpower3 no soporta Zen 4/5; el voltaje "
                      "viene de la tabla PM del SMU).", group="Sensores"))
    elif zp and zp.installed and "k10temp" in si.hwmon:
        add(Check("zenpower3 instalado pero no activo", WARN,
                  "k10temp está usando el sensor y zenpower no puede cargarse. Bloquea k10temp:",
                  "echo 'blacklist k10temp' | sudo tee /etc/modprobe.d/k10temp-blacklist.conf\n"
                  "sudo modprobe -r k10temp && sudo modprobe zenpower", group="Sensores"))
    elif zp and zp.installed:
        add(Check("zenpower3 instalado pero no cargado", WARN, "Cárgalo y hazlo permanente:",
                  "sudo modprobe zenpower\necho zenpower | sudo tee /etc/modules-load.d/zenpower.conf",
                  group="Sensores"))
    else:
        add(Check("zenpower3 no instalado (opcional)", INFO,
                  "Con k10temp solo hay temperatura. zenpower3 agrega voltaje, corriente y potencia SVI2:",
                  "yay -S zenpower3-dkms", group="Sensores"))
    if "k10temp" not in si.hwmon and "zenpower" not in si.hwmon:
        add(Check("Sin sensor de temperatura", ERROR, "No hay k10temp ni zenpower. Carga el sensor del kernel:",
                  "sudo modprobe k10temp", group="Sensores"))
    if service_running:
        if feats.get("rapl"):
            add(Check("RAPL (potencia del paquete)", OK, "Medición de energía del procesador.", group="Sensores"))
        else:
            add(Check("RAPL no disponible", INFO, "El kernel no expone la energía del paquete; se usa la tabla "
                      "PM o zenpower para la potencia.", group="Sensores"))

    # --- ryzen_smu ---
    smu = mods.get("ryzen_smu")
    if smu and smu.loaded:
        if service_running and not smu_st.get("test_ok"):
            add(Check("ryzen_smu cargado pero el SMU no responde", ERROR,
                      "La prueba de comunicación con el SMU falló. Puede ser una versión del módulo que no "
                      "soporta tu procesador o un bloqueo del kernel.", "sudo dmesg | grep -i smu", group="SMU"))
        elif service_running:
            add(Check("ryzen_smu funcionando", OK,
                      f"SMU {smu_st.get('version') or '?'} · módulo {smu_st.get('drv_version') or '?'}",
                      group="SMU"))
        else:
            add(Check("ryzen_smu cargado", OK, "Activa el servicio para usarlo.", group="SMU"))
        if "ryzen_smu" not in si.autoload:
            add(Check("ryzen_smu no se carga al iniciar", WARN,
                      "Después de reiniciar el módulo no estará y los perfiles de arranque no se aplicarán:",
                      "echo ryzen_smu | sudo tee /etc/modules-load.d/ryzen_smu.conf", group="SMU"))
    elif smu and smu.installed:
        add(Check("ryzen_smu instalado pero no cargado", WARN, "Sin él no hay PBO, Curve Optimizer, OC manual "
                  "ni tabla PM. Cárgalo y hazlo permanente:",
                  "sudo modprobe ryzen_smu\necho ryzen_smu | sudo tee /etc/modules-load.d/ryzen_smu.conf",
                  group="SMU"))
    else:
        add(Check("ryzen_smu no instalado", WARN,
                  "Es el módulo del kernel que permite hablar con el SMU del procesador (lo mismo que hace "
                  "Ryzen Master en Windows). Sin él solo funcionan el monitor y los ajustes de cpufreq.",
                  "yay -S ryzen_smu-dkms-git\nsudo modprobe ryzen_smu\n"
                  "echo ryzen_smu | sudo tee /etc/modules-load.d/ryzen_smu.conf", group="SMU"))
    if si.lockdown and si.lockdown != "none":
        add(Check(f"Kernel lockdown ({si.lockdown})", WARN,
                  "Con Secure Boot / lockdown el kernel puede negarse a cargar módulos sin firma como "
                  "ryzen_smu o zenpower, o bloquear su acceso al hardware.", group="SMU"))
    if service_running and smu_st.get("test_ok"):
        if smu_st.get("pm_known"):
            add(Check("Tabla PM reconocida", OK, f"Versión {smu_st.get('pm_version', 0):#08x}: PPT/TDC/EDC en "
                      "vivo, relojes de memoria y datos por núcleo.", group="SMU"))
        elif smu_st.get("pm_version"):
            add(Check("Tabla PM no reconocida", INFO, f"Versión {smu_st['pm_version']:#08x} sin mapa conocido: "
                      "sin PPT/TDC/EDC en vivo ni valores de fábrica. Los ajustes siguen funcionando.",
                      group="SMU"))
        cm = status.get("coremap", {})
        if not cm.get("verified"):
            add(Check("Mapa de núcleos aproximado", WARN, f"{cm.get('note', '')}. El Curve Optimizer por núcleo "
                      "queda desactivado (sí funciona el de todos los núcleos).", group="SMU"))
        probe = status.get("co_probe")
        if probe not in (None, "ok", "n/a") and feats.get("co"):
            add(Check("No se pudo leer el Curve Optimizer actual", WARN,
                      f"{probe}. Puede que tu BIOS no exponga Curve Optimizer; al aplicar verás si el SMU lo "
                      "acepta.", group="SMU"))
        if status.get("experimental"):
            add(Check("Funciones experimentales activas", WARN,
                      "Se usan comandos del SMU sin validación pública para tu procesador.", group="SMU"))

    # --- Asistente ---
    import shutil
    if shutil.which("stress-ng"):
        add(Check("stress-ng instalado", OK, "Disponible para el Asistente OC.", group="Asistente OC"))
    else:
        add(Check("stress-ng no instalado (para el Asistente OC)", INFO,
                  "El asistente lo usa para probar cada frecuencia verificando los cálculos:",
                  "sudo pacman -S stress-ng", group="Asistente OC"))

    # --- cpufreq ---
    f = si.cpufreq
    if f.driver == "amd-pstate-epp":
        add(Check("amd-pstate en modo activo (EPP)", OK, "Control de preferencia de energía disponible.",
                  group="Frecuencia"))
    elif f.driver == "amd-pstate":
        add(Check(f"amd-pstate en modo {f.pstate_status or 'pasivo'}", INFO,
                  "Para usar la preferencia de energía (EPP) cambia a modo activo:",
                  "echo active | sudo tee /sys/devices/system/cpu/amd_pstate/status", group="Frecuencia"))
    elif f.driver == "acpi-cpufreq":
        if gen in ("Zen", "Zen+"):
            add(Check("acpi-cpufreq", INFO, f"{gen} no soporta amd-pstate; se usan gobernador y boost.",
                      group="Frecuencia"))
        else:
            add(Check("Se usa acpi-cpufreq en lugar de amd-pstate", WARN,
                      "amd-pstate da mejor control de frecuencia y EPP. Necesita CPPC activado en la BIOS y, "
                      "en kernels viejos, el parámetro amd_pstate=active. " + BIOS_CPPC_PATH,
                      "# /etc/default/grub → GRUB_CMDLINE_LINUX_DEFAULT=\"... amd_pstate=active\"\n"
                      "sudo grub-mkconfig -o /boot/grub/grub.cfg", group="Frecuencia"))
    elif f.driver is None:
        add(Check("Sin cpufreq", WARN, "El kernel no expone control de frecuencia.", group="Frecuencia"))
    if si.cores and not any(c.rank for c in si.cores) and gen not in ("Zen", "Zen+"):
        add(Check("Sin ranking de núcleos preferidos", INFO,
                  "No se puede marcar el mejor núcleo. " + BIOS_CPPC_PATH, group="Frecuencia"))

    # --- Capacidades ---
    def cap(name, ok, reason, partial=False):
        r.caps.append(Capability(name, bool(ok), reason, partial))

    temp = "zenpower" in si.hwmon or "k10temp" in si.hwmon
    cap("Monitor (uso, frecuencia, temperatura)", temp, "OK" if temp else "falta un sensor de temperatura")
    cap("Voltaje y corriente", "zenpower" in si.hwmon or feats.get("pm"),
        "zenpower3" if "zenpower" in si.hwmon else ("tabla PM" if feats.get("pm") else
                                                     "necesita zenpower3 o ryzen_smu"))
    cap("Potencia del procesador", feats.get("rapl") or feats.get("pm") or "zenpower" in si.hwmon,
        "RAPL" if feats.get("rapl") else ("tabla PM" if feats.get("pm") else
                                          ("SVI2" if "zenpower" in si.hwmon else "necesita el servicio")))
    cap("Datos por núcleo (W, °C, V)", feats.get("pm"), "tabla PM" if feats.get("pm") else
        "necesita ryzen_smu y una tabla PM conocida")
    cap("Límites PBO (PPT/TDC/EDC)", feats.get("limits"),
        "SMU" if feats.get("limits") else _why_smu(smu, service_running, smu_st, status, "límites"))
    cap("Curve Optimizer", feats.get("co"),
        ("por núcleo" if feats.get("co_per_core") else "solo todos los núcleos") if feats.get("co")
        else _why_co(gen, smu, service_running, smu_st, status),
        partial=feats.get("co") and not feats.get("co_per_core"))
    cap("OC manual (frecuencia y voltaje fijos)", feats.get("oc"),
        "SMU" if feats.get("oc") else _why_smu(smu, service_running, smu_st, status, "oc"))
    cap("Boost on/off", bool(f.boost_path), "cpufreq" if f.boost_path else "el driver no lo permite")
    cap("Preferencia de energía (EPP)", bool(f.epps), "amd-pstate activo" if f.epps else "necesita amd-pstate activo")
    cap("Núcleos preferidos (estrella)", any(c.rank for c in si.cores),
        "CPPC" if any(c.rank for c in si.cores) else "activa CPPC Preferred Cores en la BIOS")
    return r


def _why_smu(smu, running, smu_st, status, what) -> str:
    if not smu or not smu.installed:
        return "falta ryzen_smu"
    if not smu.loaded:
        return "ryzen_smu no está cargado"
    if not running:
        return "el servicio está detenido"
    if not smu_st.get("test_ok"):
        return "el SMU no responde"
    if not smu_st.get("profile"):
        return "sin comandos conocidos para este procesador"
    return "experimental: actívalo en Diagnóstico" if not status.get("experimental") else "no soportado"


def _why_co(gen, smu, running, smu_st, status) -> str:
    if gen in ("Zen", "Zen+", "Zen 2"):
        return f"{gen} no tiene Curve Optimizer"
    return _why_smu(smu, running, smu_st, status, "co")
