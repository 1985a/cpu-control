"""Especificaciones por modelo de Ryzen y perfiles recomendados.

Los perfiles solo usan valores que ese procesador maneja:
  * Fábrica y Eco: límites oficiales de AMD para su TDP (PPT/TDC/EDC del socket).
  * El resto se calcula a partir de los límites de fábrica de ESE chip y nunca pasa
    del «tope seguro», que depende del modelo (los X3D no se suben) y de la placa
    (A320/A520/B350/A620 tienen VRM de entrada).
  * El Curve Optimizer de los perfiles es moderado y deja a los mejores núcleos (CPPC)
    5 puntos menos agresivos, porque suelen tolerar menos undervolt. Cada chip es distinto:
    hay que probar la estabilidad.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Límites de socket de AMD por TDP: (PPT W, TDC A, EDC A)
SOCKET_LIMITS = {
    ("AM4", 65): (88, 60, 90),
    ("AM4", 105): (142, 95, 140),
    ("AM5", 65): (88, 75, 150),
    ("AM5", 105): (142, 110, 170),
    ("AM5", 120): (162, 120, 180),
    ("AM5", 170): (230, 160, 225),
}
# Modo Eco oficial de AMD: TDP origen → TDP destino
ECO = {("AM4", 65): 45, ("AM4", 105): 65, ("AM5", 105): 65, ("AM5", 170): 105}
ECO_LIMITS = {("AM4", 45): (60, 45, 65)}

# modelo → (TDP W, Tjmax °C, boost máx. GHz, núcleos)
MODELS = {
    # Zen 2
    "3100": (65, 95, 3.9, 4), "3300X": (65, 95, 4.3, 4), "3500X": (65, 95, 4.1, 6), "3600": (65, 95, 4.2, 6),
    "3700X": (65, 95, 4.4, 8), "3800X": (105, 95, 4.5, 8), "3800XT": (105, 95, 4.7, 8), "3900X": (105, 95, 4.6, 12),
    "3900XT": (105, 95, 4.7, 12), "3950X": (105, 95, 4.7, 16), "4600G": (65, 95, 4.2, 6), "4700G": (65, 95, 4.4, 8),
    # Zen 3
    "5500": (65, 90, 4.2, 6), "5600": (65, 90, 4.4, 6), "5600X": (65, 95, 4.6, 6), "5600G": (65, 95, 4.4, 6),
    "5600GT": (65, 95, 4.6, 6), "5600T": (65, 90, 4.5, 6), "5600XT": (65, 90, 4.7, 6), "5700": (65, 90, 4.6, 8),
    "5700X": (65, 90, 4.6, 8), "5700G": (65, 95, 4.6, 8), "5800": (65, 90, 4.6, 8), "5800X": (105, 90, 4.7, 8),
    "5900X": (105, 90, 4.8, 12), "5900XT": (105, 90, 4.8, 16), "5950X": (105, 90, 4.9, 16),
    "5600X3D": (105, 89, 4.4, 6), "5700X3D": (105, 90, 4.1, 8), "5800X3D": (105, 90, 4.5, 8),
    # Zen 4
    "7600": (65, 95, 5.1, 6), "7600X": (105, 95, 5.3, 6), "7700": (65, 95, 5.3, 8), "7700X": (105, 95, 5.4, 8),
    "7900": (65, 95, 5.4, 12), "7900X": (170, 95, 5.6, 12), "7950X": (170, 95, 5.7, 16),
    "7800X3D": (120, 89, 5.0, 8), "7900X3D": (120, 89, 5.6, 12), "7950X3D": (120, 89, 5.7, 16),
    "8500G": (65, 95, 5.0, 6), "8600G": (65, 95, 5.0, 6), "8700G": (65, 95, 5.1, 8),
    # Zen 5
    "9600": (65, 95, 5.2, 6), "9600X": (65, 95, 5.4, 6), "9700X": (65, 95, 5.5, 8), "9900X": (120, 95, 5.6, 12),
    "9950X": (170, 95, 5.7, 16), "9800X3D": (120, 95, 5.2, 8), "9900X3D": (120, 95, 5.5, 12),
    "9950X3D": (170, 95, 5.7, 16),
}

LOW_VRM_CHIPSETS = {"A320", "A520", "B350", "A620"}


@dataclass
class Spec:
    model: str | None            # "5500"
    known: bool
    socket: str | None
    tdp: int | None
    tjmax: int | None
    boost_ghz: float | None
    x3d: bool = False
    stock: tuple | None = None   # (PPT, TDC, EDC) de AMD
    cap: tuple | None = None     # tope seguro (PPT, TDC, EDC) para sliders y perfiles
    cap_reason: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("model", "known", "socket", "tdp", "tjmax", "boost_ghz", "x3d",
                                              "stock", "cap", "cap_reason", "notes")}


def parse_model(model_name: str) -> str | None:
    m = re.search(r"Ryzen\s+\d+\s+(?:PRO\s+)?(\d{4}[A-Z0-9]*)", model_name, re.I)
    if not m:
        return None
    return m.group(1).upper()


def spec_for(model_name: str, generation: str | None, chipset: str | None) -> Spec:
    model = parse_model(model_name)
    data = MODELS.get(model) if model else None
    socket = None
    if generation:
        socket = "AM5" if generation.startswith(("Zen 4", "Zen 5")) else "AM4"
    if not data:
        return Spec(model, False, socket, None, None, None,
                    notes=["Modelo sin datos en la tabla: solo se ofrecen perfiles relativos a los límites que "
                           "lee el servicio."])
    tdp, tjmax, boost, _cores = data
    x3d = "X3D" in model
    s = Spec(model, True, socket, tdp, tjmax, boost, x3d, SOCKET_LIMITS.get((socket, tdp)))
    if s.stock:
        if x3d:
            factor, s.cap_reason = 1.0, "los X3D son sensibles al voltaje: no se suben sus límites"
        elif chipset in LOW_VRM_CHIPSETS:
            factor, s.cap_reason = 1.2, f"placa {chipset} con VRM de entrada: tope al 120 % de fábrica"
        elif chipset == "B450":
            factor, s.cap_reason = 1.3, "placa B450: tope al 130 % de fábrica"
        else:
            factor, s.cap_reason = 1.4, "tope al 140 % de fábrica"
        s.cap = tuple(round(v * factor) for v in s.stock)
    if model == "5800X3D":
        s.notes.append("El 5800X3D tiene PBO y overclock bloqueados por AMD; solo admite Curve Optimizer negativo.")
    return s


@dataclass
class Preset:
    id: str
    name: str
    summary: str
    risk: str                    # "bajo" | "medio"
    values: dict                 # campos de Profile
    needs: list[str] = field(default_factory=list)   # funciones necesarias
    detail: str = ""


def _co(base: int, cores: list, rng: int, per_core: bool) -> dict:
    base = max(-rng, min(rng, base))
    if not per_core:
        return {"co_mode": "all", "co_all": base}
    ranks = sorted((c for c in cores if c.rank), key=lambda c: c.rank)
    best = {c.index for c in ranks[:2]}
    return {"co_mode": "per_core",
            "co_cores": [min(0, base + 5) if c.index in best else base for c in cores]}


def build(spec: Spec, stock: dict, feats: dict, cores: list, co_range: int, epps: list[str],
          oc_cap: float | None = None) -> list[Preset]:
    """Perfiles para ESTE procesador. `stock` = valores leídos por el servicio (tabla PM) si los hay."""
    ppt = stock.get("ppt") or (spec.stock[0] if spec.stock else None)
    tdc = stock.get("tdc") or (spec.stock[1] if spec.stock else None)
    edc = stock.get("edc") or (spec.stock[2] if spec.stock else None)
    cap = spec.cap or ((round(ppt * 1.3), round(tdc * 1.3), round(edc * 1.3)) if ppt and tdc and edc else None)
    per_core = bool(feats.get("co_per_core"))
    has_co = bool(feats.get("co")) and co_range > 0
    zen4 = co_range >= 50
    out: list[Preset] = []

    def lim(p, t, e):
        if cap:
            p, t, e = min(p, cap[0]), min(t, cap[1]), min(e, cap[2])
        return {"ppt": round(p), "tdc": round(t), "edc": round(e)}

    def epp(name):
        return {"epp": name} if name in epps else {}

    if ppt and tdc and edc:
        eco_tdp = ECO.get((spec.socket, spec.tdp)) if spec.known else None
        eco_vals = ECO_LIMITS.get((spec.socket, eco_tdp)) or SOCKET_LIMITS.get((spec.socket, eco_tdp))
        quiet = lim(ppt * 0.55, tdc * 0.6, edc * 0.7)
        v = {"mode": "pbo", **quiet, "tctl": min(80, (spec.tjmax or 90) - 10), **epp("balance_power")}
        if has_co:
            v.update(_co(-20 if zen4 else -10, cores, co_range, per_core))
        out.append(Preset("silencioso", "Silencioso", f"PPT {quiet['ppt']} W · tope {v['tctl']} °C"
                          + (" · CO moderado" if has_co else ""), "bajo", v, ["limits"],
                          "Mínimo consumo y ruido. Pierde rendimiento multinúcleo bajo carga pesada; en uso "
                          "normal y juegos ligeros casi no se nota."))
        if eco_vals:
            e = dict(zip(("ppt", "tdc", "edc"), eco_vals, strict=False))
            out.append(Preset("eco", f"Eco {eco_tdp} W (AMD)", f"PPT {e['ppt']} · TDC {e['tdc']} · EDC {e['edc']}",
                              "bajo", {"mode": "eco", **e, **epp("balance_power")}, ["limits"],
                              f"El modo Eco oficial de AMD para un procesador de {spec.tdp} W: mismos límites que "
                              f"un modelo de {eco_tdp} W."))
        eff = lim(ppt * 0.8, tdc * 0.85, edc * 0.9)
        v = {"mode": "pbo", **eff, **epp("balance_performance")}
        if has_co:
            v.update(_co(-25 if zen4 else -15, cores, co_range, per_core))
        out.append(Preset("eficiente", "Eficiente (recomendado)", f"PPT {eff['ppt']} W"
                          + (f" · CO {'−25' if zen4 else '−15'}" if has_co else ""), "bajo", v, ["limits"],
                          "Menos consumo y temperatura con un rendimiento muy cercano al de fábrica: el "
                          "Curve Optimizer recupera frecuencia al bajar el voltaje. Prueba la estabilidad."))
        out.append(Preset("fabrica", "Fábrica", f"PPT {round(ppt)} · TDC {round(tdc)} · EDC {round(edc)}", "bajo",
                          {"mode": "stock"}, [], "Lo que trae la BIOS, sin cambios."))
        if not spec.x3d and cap and cap[0] > ppt:
            perf = lim(ppt * 1.15, tdc * 1.15, edc * 1.15)
            v = {"mode": "pbo", **perf, **epp("balance_performance")}
            if has_co:
                v.update(_co(-15 if zen4 else -10, cores, co_range, per_core))
            out.append(Preset("rendimiento", "Rendimiento", f"PPT {perf['ppt']} · TDC {perf['tdc']} · "
                              f"EDC {perf['edc']}", "medio", v, ["limits"],
                              "Un poco más de potencia que de fábrica para sostener el boost en cargas largas. "
                              "Necesita buena refrigeración."))
            if cap[0] >= ppt * 1.3:
                top = lim(*cap)
                v = {"mode": "pbo", **top, **epp("performance")}
                if has_co:
                    v.update(_co(-10, cores, co_range, per_core))
                out.append(Preset("maximo", "Máximo PBO", f"PPT {top['ppt']} · TDC {top['tdc']} · EDC {top['edc']}",
                                  "medio", v, ["limits"],
                                  "El tope seguro para este procesador en esta placa. Más calor y consumo por "
                                  "una ganancia pequeña; solo con disipador de torre o líquida."))
    elif has_co:
        # Sin límites conocidos: solo Curve Optimizer
        out.append(Preset("co", "Solo undervolt", f"CO {'−25' if zen4 else '−15'}", "bajo",
                          {"mode": "pbo", **_co(-25 if zen4 else -15, cores, co_range, per_core)}, ["co"],
                          "Baja el voltaje sin cambiar los límites de potencia."))
    if spec.x3d and has_co and not any(p.id == "co" for p in out):
        out.append(Preset("co", "Undervolt X3D", "CO −20", "bajo",
                          {"mode": "pbo", **_co(-20, cores, co_range, per_core)}, ["co"],
                          "Los X3D ganan mucho con un Curve Optimizer negativo: menos temperatura y más "
                          "frecuencia sostenida."))
    # Guía de michaelnager (r/ryzen): OC manual con tope de voltaje; la frecuencia la busca el asistente
    if oc_cap and not spec.x3d and spec.model != "5800X3D":
        start = int(max(2500, (spec.boost_ghz or 4.0) * 1000 - 200) // 25 * 25)
        out.append(Preset("guia", f"Manual {oc_cap:.2f} V (guía r/ryzen)", f"{start} MHz · {oc_cap:.2f} V máx.",
                          "medio", {"mode": "manual", "oc_mhz": start, "oc_volts": oc_cap}, ["oc"],
                          "Voltaje fijo con tope seguro y la frecuencia más alta estable en todos los núcleos. "
                          "Carga un punto de partida; usa el Asistente OC para encontrar la de tu chip."))
    # Sin ryzen_smu: perfil de solo cpufreq que siempre funciona
    out.append(Preset("ahorro", "Ahorro sin SMU", "Sin boost · EPP power", "bajo",
                      {"mode": "eco", "boost": False, **epp("power")}, ["boost"],
                      "Apaga el Precision Boost: baja mucho el consumo y la temperatura, a costa de rendimiento. "
                      "Funciona sin ryzen_smu."))
    return out
