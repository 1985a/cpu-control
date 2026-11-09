"""Interfaz GTK4 + libadwaita (solo Wayland)."""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import shutil
import sys
import time
from collections import deque
from typing import ClassVar

os.environ["GDK_BACKEND"] = "wayland"

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from . import APP_ID, __version__, cpudb, diagnostics, presets, stability, system
from .paths import BOOT_FILE, SESSION_FILE, STATUS_FILE, P
from .profile import (Profile, ValidationError, load_user_profiles, preset, save_user_profiles,
                      validate)
from .sensors import Sample, Sampler
from .widgets import ACCENT, BLUE, ORANGE, PINK, VIOLET, LineChart, draw_text, rgb

HELPER = "/usr/lib/cpu-control/cpu-control-helper"
SAMPLE_MS = 500
HISTORY_S = 30 * 60
MOCK = bool(os.environ.get("CPU_CONTROL_SYSROOT"))

CSS = """
window.cpu { background-color: #0e1014; color: #e8ecf1; }
.cpu headerbar { background-color: #12151a; box-shadow: inset 0 -1px #252b34; }
.sidebar { background-color: #101318; border-right: 1px solid #252b34; }
.sidebar row { border-radius: 8px; padding: 10px 12px; margin: 2px 0; color: #b4bdc9; }
.sidebar row:selected { background-color: #1b2028; color: #e8ecf1; font-weight: 600; }
.sidebar row:selected image { color: #ff7a45; }
.panel { background-color: #161a20; border: 1px solid #252b34; border-radius: 14px; padding: 16px 18px; }
.tile { background-color: #161a20; border: 1px solid #252b34; border-radius: 12px; padding: 12px 14px; }
.tile-title { color: #9aa4b2; font-size: 12px; }
.tile-value { font-family: "JetBrains Mono", monospace; font-weight: 600; font-size: 24px; }
.tile-sub { color: #8b95a3; font-size: 11px; font-family: "JetBrains Mono", monospace; }
.tile-missing { color: #ffc77a; font-size: 11px; }
.mono { font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; }
.section-title { font-size: 14px; font-weight: 600; color: #c7cfda; }
.dim { color: #9aa4b2; }
.small { font-size: 11px; }
.pill { background-color: #1f252e; color: #c7cfda; border-radius: 999px; padding: 3px 10px;
        font-family: "JetBrains Mono", monospace; font-size: 12px; }
.pill.ok { background-color: #15241d; color: #7ff0bd; }
.pill.warn { background-color: #2a2116; color: #ffc77a; }
.pill.err { background-color: #2a1717; color: #ff9b9b; }
.pill.accent { background-color: #2a1a14; color: #ffb08f; }
.bar trough { min-height: 6px; border-radius: 3px; background-color: #252b34; }
.bar progress { min-height: 6px; border-radius: 3px; background-color: #ff7a45; }
.bar.orange progress { background-color: #ffb454; }
.bar.pink progress { background-color: #f472b6; }
.bar.blue progress { background-color: #6aa8ff; }
.bar.violet progress { background-color: #a78bfa; }
.core-card { background-color: #1b2028; border: 1px solid #2c333e; border-radius: 10px; padding: 10px 12px; }
.core-card.best { border-color: #ff7a45; }
.core-name { font-size: 13px; font-weight: 600; }
.core-freq { font-family: "JetBrains Mono", monospace; font-size: 19px; font-weight: 600; }
.star { color: #ff7a45; font-size: 14px; }
.spec-row { border-bottom: 1px solid #222831; padding: 6px 0; }
.limiter { background-color: #1b2028; border-radius: 10px; padding: 10px 12px; }
.statusbar { background-color: #101318; border-top: 1px solid #252b34; padding: 6px 16px; color: #8b95a3;
             font-size: 12px; }
.check-row { padding: 10px 4px; border-bottom: 1px solid #222831; }
.fix { background-color: #0e1014; border: 1px solid #252b34; border-radius: 8px; padding: 8px 10px;
       font-family: "JetBrains Mono", monospace; font-size: 12px; color: #e8ecf1; }
.icon-ok { color: #3ddc97; } .icon-warn { color: #ffc77a; } .icon-error { color: #ff8a8a; }
.icon-info { color: #6aa8ff; }
.unavailable { color: #ffc77a; font-size: 12px; }
.result-ok { color: #7ff0bd; } .result-err { color: #ff9b9b; }
.co-row { background-color: #1b2028; border-radius: 10px; padding: 4px 10px; }
.preset { background-color: #1b2028; border: 1px solid #2c333e; border-radius: 12px; padding: 12px 14px; }
.preset.rec { border-color: #ff7a45; }
.preset-name { font-weight: 600; font-size: 14px; }
.warnbox { background-color: #2a2116; color: #ffd9a3; border-radius: 10px; padding: 10px 12px; }
"""
CSS_ACCENT_OLD = "@define-color accent_bg_color #ff7a45; @define-color accent_color #ff8a5c; " \
                 "@define-color accent_fg_color #1a0d07;"
CSS_ACCENT_VARS = ":root { --accent-bg-color: #ff7a45; --accent-color: #ff8a5c; --accent-fg-color: #1a0d07; }"

ICONS = {"ok": "emblem-ok-symbolic", "warn": "dialog-warning-symbolic", "error": "dialog-error-symbolic",
         "info": "dialog-information-symbolic"}


def fmt(v, suffix="", digits=0, none="—"):
    if v is None:
        return none
    s = f"{v:.{digits}f}" if isinstance(v, float) or digits else str(v)
    if digits == 0 and abs(float(v)) >= 1000:
        s = f"{round(v):,}".replace(",", " ")
    return s + suffix


def lbl(text="", css=(), xalign=0.0, **kw) -> Gtk.Label:
    return Gtk.Label(label=text, xalign=xalign, css_classes=list(css), **kw)


def clear(box: Gtk.Widget):
    child = box.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def panel(title: str | None = None, spacing=12) -> Gtk.Box:
    b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing, css_classes=["panel"])
    if title:
        b.append(lbl(title, ["section-title"]))
    return b


def scrolled(child) -> Gtk.ScrolledWindow:
    sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    sw.set_child(child)
    return sw


def page_box() -> Gtk.Box:
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_top=20, margin_bottom=20,
                   margin_start=24, margin_end=24)


# --- Widgets ---------------------------------------------------------------
class Tile(Gtk.Box):
    def __init__(self, title: str, color_css: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["tile"], hexpand=True)
        self.append(lbl(title, ["tile-title"]))
        self.value = lbl("—", ["tile-value"])
        self.append(self.value)
        self.bar = Gtk.ProgressBar(css_classes=["bar", color_css], margin_top=4)
        self.append(self.bar)
        self.sub = lbl("", ["tile-sub"], ellipsize=Pango.EllipsizeMode.END)
        self.append(self.sub)
        self.update_property([Gtk.AccessibleProperty.LABEL], [title])

    def set(self, value: str, frac: float | None, sub: str = "", missing: str | None = None):
        self.value.set_label(value)
        self.bar.set_fraction(max(0.0, min(1.0, frac)) if frac is not None else 0)
        self.sub.set_label(missing or sub)
        self.sub.set_css_classes(["tile-missing"] if missing else ["tile-sub"])


class ThreadBars(Gtk.DrawingArea):
    def __init__(self, n: int):
        super().__init__(content_height=40, hexpand=True)
        self.values = [0.0] * n
        self.set_draw_func(self._draw)

    def set(self, values):
        self.values = list(values)
        self.queue_draw()

    def _draw(self, area, cr, w, h):
        n = max(1, len(self.values))
        gap = 6
        bw = (w - gap * (n - 1)) / n
        for i, v in enumerate(self.values):
            x = i * (bw + gap)
            cr.set_source_rgba(*rgb("#252b34"))
            cr.rectangle(x, 0, bw, h)
            cr.fill()
            hh = h * max(0.0, min(100.0, v)) / 100
            cr.set_source_rgba(*rgb(ACCENT, 0.95 if i == 0 else 0.6))
            cr.rectangle(x, h - hh, bw, hh)
            cr.fill()


class CoreCard(Gtk.Box):
    def __init__(self, core: system.Core, best: bool):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                         css_classes=["core-card"] + (["best"] if best else []))
        self.set_size_request(150, -1)
        top = Gtk.Box(spacing=6)
        top.append(lbl(f"Núcleo {core.index}", ["core-name"], hexpand=True))
        if core.rank:
            if best:
                star = lbl("★", ["star"])
                star.set_tooltip_text("Mejor núcleo según CPPC")
                top.append(star)
            else:
                rk = lbl(f"#{core.rank}", ["dim", "small", "mono"])
                rk.set_tooltip_text("Posición en el ranking de núcleos preferidos (CPPC)")
                top.append(rk)
        self.append(top)
        self.freq = lbl("—", ["core-freq"])
        self.append(self.freq)
        self.bars = ThreadBars(len(core.threads))
        self.bars.update_property([Gtk.AccessibleProperty.LABEL], [f"Uso de los hilos del núcleo {core.index}"])
        self.append(self.bars)
        row = Gtk.Box()
        self.threads = lbl("Hilos " + ", ".join(map(str, core.threads)), ["dim", "small", "mono"], hexpand=True)
        row.append(self.threads)
        self.power = lbl("", ["dim", "small", "mono"])
        row.append(self.power)
        self.append(row)
        self.extra = lbl("", ["small", "mono"])
        self.append(self.extra)

    def update(self, cs, co: int | None):
        self.freq.set_markup(f"{cs.freq_mhz / 1000:.2f}<span size='small' foreground='#9aa4b2'> GHz</span>"
                             if cs.freq_mhz else "—")
        self.bars.set(cs.usage)
        self.power.set_label(fmt(cs.power_w, " W", 1, ""))
        parts = []
        if cs.temp_c:
            parts.append(f"{cs.temp_c:.0f} °C")
        if cs.volt:
            parts.append(f"{cs.volt:.3f} V")
        if co is not None:
            parts.append(f"CO {co:+d}")
        self.extra.set_label(" · ".join(parts))


# --- Página: Inicio ----------------------------------------------------------
class HomePage(Gtk.Box):
    def __init__(self, win: MainWindow):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        root = page_box()
        self.append(scrolled(root))

        tiles = Gtk.Grid(column_spacing=12, column_homogeneous=True)
        self.t_temp = Tile("Temperatura", "orange")
        self.t_clock = Tile("Reloj efectivo", "")
        self.t_power = Tile("Potencia", "pink")
        self.t_curr = Tile("Corriente", "blue")
        self.t_volt = Tile("Voltaje del núcleo", "violet")
        for i, t in enumerate((self.t_temp, self.t_clock, self.t_power, self.t_curr, self.t_volt)):
            tiles.attach(t, i, 0, 1, 1)
        root.append(tiles)

        self.cores_panel = panel(None, spacing=10)
        head = Gtk.Box(spacing=10)
        head.append(lbl("Núcleos", ["section-title"], hexpand=True))
        self.legend = lbl("", ["dim", "small"])
        head.append(self.legend)
        self.cores_panel.append(head)
        self.cores_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.cores_panel.append(self.cores_box)
        root.append(self.cores_panel)

        bottom = Gtk.Box(spacing=16, homogeneous=True)
        self.info = panel("Procesador", spacing=2)
        self.info_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.info.append(self.info_rows)
        bottom.append(self.info)
        mem = panel("Memoria y fabric", spacing=2)
        self.mem_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        mem.append(self.mem_rows)
        mem.append(Gtk.Box(vexpand=True))
        self.limiter = lbl("", ["limiter"], wrap=True)
        mem.append(self.limiter)
        bottom.append(mem)
        root.append(bottom)
        self.cards: list[CoreCard] = []

    @staticmethod
    def _rows(box, rows):
        clear(box)
        vals = {}
        for k, v in rows:
            r = Gtk.Box(css_classes=["spec-row"])
            r.append(lbl(k, ["dim"], hexpand=True))
            val = lbl(v, ["mono"], xalign=1, selectable=True)
            r.append(val)
            box.append(r)
            vals[k] = val
        return vals

    def setup(self, si: system.SystemInfo):
        clear(self.cores_box)
        self.cards = [None] * len(si.cores)
        best = min((c.rank for c in si.cores if c.rank), default=None)
        for gi_, grp in enumerate(si.groups):
            if len(si.groups) > 1:
                self.cores_box.append(lbl(f"{si.group_label} {gi_}", ["dim", "small"]))
            fb = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, min_children_per_line=3,
                             max_children_per_line=8, column_spacing=10, row_spacing=10)
            for idx in grp:
                c = si.cores[idx]
                card = CoreCard(c, best is not None and c.rank == best)
                fb.append(card)
                self.cards[idx] = card
            self.cores_box.append(fb)
        self.legend.set_label("★ mejor núcleo (CPPC) · #n posición en el ranking" if best else
                              "Ranking de núcleos no disponible (activa CPPC en la BIOS)")
        p = si.platform
        f = si.cpufreq
        rows = [("Código", f"{p.codename} · {p.generation}" if p else "—"),
                ("Socket · proceso", f"{p.socket} · {p.process}" if p else "—"),
                ("Núcleos / hilos", f"{len(si.cores)} / {si.threads}"),
                ("Caché L1 / L2 / L3", f"{fmt(si.l1d_kb, ' KB')} · {fmt((si.l2_kb or 0) / 1024, ' MB', 1)} · "
                                       f"{fmt(si.l3_mb, ' MB')}"),
                ("Frecuencia mín. / máx.", f"{fmt((f.hw_min_khz or 0) // 1000, ' MHz')} / "
                                           f"{fmt((f.hw_max_khz or 0) // 1000, ' MHz')}" if f.hw_max_khz else "—"),
                ("Familia · modelo · stepping", f"{si.family:#x} · {si.model:#x} · {si.stepping}"),
                ("Microcódigo", si.microcode),
                ("Driver de frecuencia", f"{f.driver or '—'}" + (f" ({f.pstate_status})" if f.pstate_status else ""))]
        self._rows(self.info_rows, rows)

    def update(self, x: Sample):
        si, st = self.win.si, self.win.status
        lim = (st.get("limits") or {})
        stock = st.get("stock", {})
        tmax = (x.thm[1] if x.thm and x.thm[1] else None) or stock.get("tctl") or 95
        miss_temp = None if x.temp is not None else "Falta k10temp o zenpower"
        self.t_temp.set(fmt(x.temp, " °C"), (x.temp or 0) / tmax, f"Límite {tmax:.0f} °C" +
                        (f" · CCD {', '.join(f'{t:.0f}' for t in x.tccd)}" if x.tccd else ""), miss_temp)
        hwmax = (si.cpufreq.hw_max_khz or 0) / 1000 or None
        self.t_clock.set(f"{x.freq_avg / 1000:.2f} GHz" if x.freq_avg else "—",
                         (x.freq_avg or 0) / hwmax if hwmax else None,
                         f"Pico {x.freq_max / 1000:.2f} GHz · {x.sources.get('freq')}" if x.freq_max else "")
        pw = x.power
        ppt_lim = x.ppt[1] if x.ppt and x.ppt[1] else stock.get("ppt")
        self.t_power.set(fmt(pw, " W", 1), (pw or 0) / ppt_lim if ppt_lim else None,
                         (f"PPT {ppt_lim:.0f} W · " if ppt_lim else "") + (x.sources.get("power") or ""),
                         None if pw is not None else "Activa el servicio o instala zenpower3")
        if x.tdc and x.tdc[0] is not None:
            edc = f" · EDC {x.edc[0]:.0f}/{x.edc[1]:.0f} A" if x.edc and x.edc[0] is not None else ""
            self.t_curr.set(f"{x.tdc[0]:.0f} A", x.tdc[0] / x.tdc[1] if x.tdc[1] else None,
                            f"TDC {x.tdc[1]:.0f} A{edc}")
        else:
            self.t_curr.set(fmt(x.icore, " A", 1), (x.icore or 0) / 100 if x.icore else None,
                            "SVI2 núcleo" + (f" · SoC {x.isoc:.1f} A" if x.isoc else ""),
                            None if x.icore is not None else "Necesita zenpower3 o ryzen_smu")
        self.t_volt.set(fmt(x.vcore, " V", 3), (x.vcore or 0) / 1.5 if x.vcore else None,
                        f"SoC {x.vsoc:.3f} V" if x.vsoc else "", None if x.vcore else "Necesita zenpower3 o ryzen_smu")

        co = st.get("co_current") or []
        for i, (card, cs) in enumerate(zip(self.cards, x.cores, strict=False)):
            card.update(cs, co[i] if i < len(co) else None)

        if x.mclk:
            ratio = "1:1" if x.fclk and abs(x.fclk - x.mclk) < 5 else (f"FCLK {x.fclk:.0f} ≠ MCLK" if x.fclk else "—")
            rows = [("Memoria", f"DDR · {x.mclk * 2:.0f} MT/s"), ("FCLK", fmt(x.fclk, " MHz")),
                    ("UCLK", fmt(x.uclk, " MHz")), ("MCLK", fmt(x.mclk, " MHz")), ("Relación", ratio)]
        else:
            rows = [("Relojes de memoria", "necesitan ryzen_smu y una tabla PM conocida")]
        self._rows(self.mem_rows, rows)
        lim_txt = x.limiter()
        if lim_txt:
            self.limiter.set_markup(f"Limitado por: <b>{GLib.markup_escape_text(lim_txt)}</b>")
        elif x.ppt:
            self.limiter.set_markup("Limitado por: <b>nada</b> — rinde dentro de sus límites")
        else:
            self.limiter.set_markup("<span foreground='#9aa4b2'>Sin datos de límites (necesita tabla PM)</span>")
        _ = lim


# --- Página: Historial -------------------------------------------------------
class History:
    def __init__(self):
        self.maxlen = HISTORY_S * 1000 // SAMPLE_MS + 10
        self.s: dict[str, deque] = {}

    def add(self, k, t, v):
        if v is not None:
            self.s.setdefault(k, deque(maxlen=self.maxlen)).append((t, float(v)))

    def get(self, k):
        return list(self.s.get(k, ()))


class HistoryPage(Gtk.Box):
    SERIES: ClassVar = [("temp", "Temperatura", ORANGE, "°C", 100), ("power", "Potencia", PINK, "W", None),
              ("freq", "Reloj efectivo promedio", ACCENT, "MHz", None), ("usage", "Uso total", ACCENT, "%", 100),
              ("vcore", "Voltaje del núcleo", VIOLET, "V", 1.6), ("curr", "Corriente del núcleo", BLUE, "A", None)]

    def __init__(self, win: MainWindow):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.window_s = 300
        root = page_box()
        self.append(scrolled(root))
        bar = Gtk.Box(spacing=10)
        bar.append(lbl("Historial", ["section-title"], hexpand=True))
        seg = Gtk.Box(css_classes=["linked"])
        first = None
        for text, secs in (("1 min", 60), ("5 min", 300), ("30 min", 1800)):
            b = Gtk.ToggleButton(label=text, group=first, active=secs == 300)
            b.connect("toggled", self._win, secs)
            first = first or b
            seg.append(b)
        bar.append(seg)
        self.rec = Gtk.ToggleButton(label="Grabar CSV")
        self.rec.connect("toggled", lambda b: win.toggle_csv(b.get_active()))
        bar.append(self.rec)
        root.append(bar)
        grid = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True)
        self.charts = {}
        for i, (k, title, color, unit, fixed) in enumerate(self.SERIES):
            card = panel(None, spacing=6)
            head = Gtk.Box()
            head.append(lbl(title, ["section-title"], hexpand=True))
            val = lbl("—", ["mono"])
            head.append(val)
            card.append(head)
            ch = LineChart(color, unit, fixed, height=120)
            card.append(ch)
            grid.attach(card, i % 2, i // 2, 1, 1)
            self.charts[k] = (ch, val, unit)
        root.append(grid)

    def _win(self, b, secs):
        if b.get_active():
            self.window_s = secs
            self.update()

    def update(self, *_):
        h, now = self.win.history, time.monotonic()
        stock = self.win.status.get("stock", {})
        x = self.win.last_sample

        def cur(pair, fallback):
            return pair[1] if pair and pair[1] else fallback
        limits = {"temp": (cur(x and x.thm, stock.get("tctl")), "límite"),
                  "power": (cur(x and x.ppt, stock.get("ppt")), "PPT"),
                  "curr": (cur(x and x.tdc, stock.get("tdc")), "TDC")}
        for k, (ch, val, unit) in self.charts.items():
            pts = h.get(k)
            lim, ll = limits.get(k, (None, ""))
            ch.set_data(pts, now, self.window_s, lim, ll)
            d = 3 if k == "vcore" else (1 if k == "power" else 0)
            val.set_label(fmt(pts[-1][1], f" {unit}", d) if pts else "sin datos")


# --- Página: Ajuste ----------------------------------------------------------
class LimitRow(Gtk.Box):
    """Fila con interruptor «personalizar», valor y deslizador."""

    def __init__(self, title, unit, lo, hi, step=1, digits=0):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        top = Gtk.Box(spacing=8)
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER, tooltip_text="Personalizar este valor")
        self.switch.update_property([Gtk.AccessibleProperty.LABEL], [f"Personalizar {title}"])
        top.append(self.switch)
        top.append(lbl(title, hexpand=True))
        self.stock = lbl("", ["dim", "small", "mono"])
        top.append(self.stock)
        self.adj = Gtk.Adjustment(lower=lo, upper=hi, step_increment=step, page_increment=step * 10)
        self.spin = Gtk.SpinButton(adjustment=self.adj, digits=digits, numeric=True, width_chars=6)
        self.spin.update_property([Gtk.AccessibleProperty.LABEL], [title])
        top.append(self.spin)
        top.append(lbl(unit, ["dim"]))
        self.append(top)
        self.scale = Gtk.Scale(adjustment=self.adj, draw_value=False, hexpand=True, digits=digits)
        self.scale.update_property([Gtk.AccessibleProperty.LABEL], [title])
        self.append(self.scale)
        self.switch.connect("notify::active", lambda *_: self._sync())
        self._sync()

    def _sync(self):
        on = self.switch.get_active()
        self.spin.set_sensitive(on)
        self.scale.set_sensitive(on)

    def set_range(self, lo, hi, stock=None, unit=""):
        self.adj.set_lower(lo)
        self.adj.set_upper(hi)
        self.scale.clear_marks()
        if stock:
            self.scale.add_mark(stock, Gtk.PositionType.BOTTOM, None)
            self.stock.set_label(f"fábrica {stock:g}{unit}")
        else:
            self.stock.set_label("fábrica desconocida")

    def get(self):
        return self.adj.get_value() if self.switch.get_active() else None

    def put(self, v, fallback=None):
        self.switch.set_active(v is not None)
        if v is not None:
            self.adj.set_value(v)
        elif fallback is not None:
            self.adj.set_value(fallback)


class TuningPage(Gtk.Box):
    MODES = (("eco", "Eco"), ("stock", "Fábrica"), ("pbo", "PBO"), ("manual", "Manual"))
    HINTS: ClassVar = {"eco": "Baja PPT/TDC/EDC parecido al modo Eco de AMD: menos calor y consumo.",
             "stock": "Deshace todo lo que cambió esta app y vuelve a los valores de la BIOS.",
             "pbo": "Ajusta límites de potencia y Curve Optimizer; el boost sigue siendo automático.",
             "manual": "Frecuencia y voltaje fijos para todos los núcleos (desactiva el boost)."}

    def __init__(self, win: MainWindow):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.profiles = load_user_profiles()
        self._loading = False
        root = page_box()
        self.append(scrolled(root))

        # Barra de modo y perfiles
        top = Gtk.Box(spacing=10, css_classes=["panel"])
        top.append(lbl("Modo", ["section-title"]))
        seg = Gtk.Box(css_classes=["linked"])
        self.mode_btns = {}
        first = None
        for key, text in self.MODES:
            b = Gtk.ToggleButton(label=text, group=first)
            b.connect("toggled", self._mode_toggled, key)
            first = first or b
            self.mode_btns[key] = b
            seg.append(b)
        top.append(seg)
        self.hint = lbl("", ["dim", "small"], wrap=True, hexpand=True)
        top.append(self.hint)
        self.prof_model = Gtk.StringList()
        self.prof_dd = Gtk.DropDown(model=self.prof_model)
        self.prof_dd.update_property([Gtk.AccessibleProperty.LABEL], ["Perfil guardado"])
        self.prof_dd.connect("notify::selected", self._profile_selected)
        top.append(self.prof_dd)
        save = Gtk.Button(label="Guardar…")
        save.connect("clicked", self._save_as)
        top.append(save)
        dele = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Eliminar perfil")
        dele.update_property([Gtk.AccessibleProperty.LABEL], ["Eliminar perfil"])
        dele.connect("clicked", self._delete)
        top.append(dele)
        root.append(top)

        self.pre_panel = panel(None, spacing=10)
        self.pre_title = lbl("", ["section-title"])
        self.pre_panel.append(self.pre_title)
        self.pre_sub = lbl("", ["dim", "small"], wrap=True)
        self.pre_panel.append(self.pre_sub)
        self.pre_box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, min_children_per_line=2,
                                   max_children_per_line=4, column_spacing=10, row_spacing=10)
        self.pre_panel.append(self.pre_box)
        self.pre_panel.append(lbl("Cargar un perfil solo llena los controles; revisa y pulsa «Aplicar». Con Curve "
                                  "Optimizer, deja «Aplicar al iniciar» apagado hasta comprobar que es estable.",
                                  ["dim", "small"], wrap=True))
        root.append(self.pre_panel)

        cols = Gtk.Box(spacing=16)
        root.append(cols)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        left.set_size_request(420, -1)
        cols.append(left)

        # Límites
        self.lim_panel = panel("Límites PBO")
        self.lim_note = lbl("", ["unavailable"], wrap=True)
        self.lim_panel.append(self.lim_note)
        self.r_ppt = LimitRow("PPT — potencia del paquete", "W", 15, 300)
        self.r_tdc = LimitRow("TDC — corriente sostenida", "A", 10, 200)
        self.r_edc = LimitRow("EDC — corriente pico", "A", 10, 300)
        self.r_tctl = LimitRow("Límite de temperatura", "°C", 60, 95)
        self.r_scalar = LimitRow("Escalar PBO", "×", 1, 10)
        self.lim_rows = (self.r_ppt, self.r_tdc, self.r_edc, self.r_tctl, self.r_scalar)
        for r in self.lim_rows:
            self.lim_panel.append(r)
        self.lim_panel.append(lbl("Apagado = no se toca ese valor. Lo que dejes de personalizar vuelve al "
                                  "valor de fábrica al aplicar.", ["dim", "small"], wrap=True))
        left.append(self.lim_panel)

        # cpufreq
        self.freq_panel = panel("Frecuencia (cpufreq / amd-pstate)")
        b = Gtk.Box(spacing=10)
        b.append(lbl("Precision Boost", hexpand=True))
        self.boost = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.boost.update_property([Gtk.AccessibleProperty.LABEL], ["Precision Boost"])
        b.append(self.boost)
        self.freq_panel.append(b)
        e = Gtk.Box(spacing=10)
        e.append(lbl("Preferencia de energía (EPP)", hexpand=True))
        self.epp = Gtk.DropDown(model=Gtk.StringList())
        self.epp.update_property([Gtk.AccessibleProperty.LABEL], ["Preferencia de energía"])
        e.append(self.epp)
        self.freq_panel.append(e)
        g = Gtk.Box(spacing=10)
        g.append(lbl("Gobernador", hexpand=True))
        self.gov = Gtk.DropDown(model=Gtk.StringList())
        self.gov.update_property([Gtk.AccessibleProperty.LABEL], ["Gobernador"])
        g.append(self.gov)
        self.freq_panel.append(g)
        mm = Gtk.Box(spacing=10)
        mm.append(lbl("Mín.", ["dim"]))
        self.fmin = Gtk.SpinButton.new_with_range(400, 6000, 25)
        self.fmin.update_property([Gtk.AccessibleProperty.LABEL], ["Frecuencia mínima"])
        mm.append(self.fmin)
        mm.append(lbl("Máx.", ["dim"]))
        self.fmax = Gtk.SpinButton.new_with_range(400, 6000, 25)
        self.fmax.update_property([Gtk.AccessibleProperty.LABEL], ["Frecuencia máxima"])
        mm.append(self.fmax)
        mm.append(lbl("MHz", ["dim"]))
        self.freq_panel.append(mm)
        self.freq_note = lbl("", ["unavailable"], wrap=True)
        self.freq_panel.append(self.freq_note)
        left.append(self.freq_panel)

        # Columna derecha: CO / manual
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, hexpand=True)
        cols.append(right)
        self.co_panel = panel(None)
        h = Gtk.Box(spacing=10)
        h.append(lbl("Curve Optimizer", ["section-title"], hexpand=True))
        seg = Gtk.Box(css_classes=["linked"])
        self.co_btns = {}
        first = None
        for key, text in (("off", "Sin cambios"), ("all", "Todos"), ("per_core", "Por núcleo")):
            bt = Gtk.ToggleButton(label=text, group=first)
            bt.connect("toggled", self._co_toggled)
            first = first or bt
            self.co_btns[key] = bt
            seg.append(bt)
        h.append(seg)
        self.co_panel.append(h)
        self.co_panel.append(lbl("Valores negativos bajan el voltaje en toda la curva: menos calor y más boost "
                                 "sostenido. Si te pasas, el equipo se cuelga o se reinicia; baja de 5 en 5 y "
                                 "prueba con carga y en reposo.", ["dim", "small"], wrap=True))
        self.co_note = lbl("", ["unavailable"], wrap=True)
        self.co_panel.append(self.co_note)
        self.co_stack = Gtk.Stack()
        self.co_stack.add_named(lbl("El Curve Optimizer queda como lo dejó la BIOS.", ["dim"]), "off")
        allb = Gtk.Box(spacing=10, css_classes=["co-row"])
        allb.append(lbl("Todos los núcleos", hexpand=False))
        self.co_all_adj = Gtk.Adjustment(lower=-30, upper=30, step_increment=1, page_increment=5)
        sc = Gtk.Scale(adjustment=self.co_all_adj, draw_value=False, hexpand=True, digits=0)
        sc.update_property([Gtk.AccessibleProperty.LABEL], ["Curve Optimizer todos los núcleos"])
        allb.append(sc)
        sp = Gtk.SpinButton(adjustment=self.co_all_adj, numeric=True, width_chars=4)
        sp.update_property([Gtk.AccessibleProperty.LABEL], ["Curve Optimizer todos los núcleos"])
        allb.append(sp)
        self.co_stack.add_named(allb, "all")
        self.co_cores_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.co_stack.add_named(self.co_cores_box, "per_core")
        self.co_panel.append(self.co_stack)
        right.append(self.co_panel)

        self.oc_panel = panel("OC manual")
        self.oc_panel.append(lbl("Fija la misma frecuencia y voltaje para todos los núcleos y desactiva el boost "
                                 "automático. Útil para pruebas; para uso diario suele rendir mejor PBO con "
                                 "Curve Optimizer.", ["dim", "small"], wrap=True))
        self.oc_note = lbl("", ["unavailable"], wrap=True)
        self.oc_panel.append(self.oc_note)
        self.r_ocf = LimitRow("Frecuencia de todos los núcleos", "MHz", 2000, 5000, step=25)
        self.r_ocv = LimitRow("Voltaje del núcleo", "V", 0.8, 1.4, step=0.00625, digits=4)
        for r in (self.r_ocf, self.r_ocv):
            r.switch.set_active(True)
            r.switch.set_visible(False)
            self.oc_panel.append(r)
        self.oc_panel.append(lbl("Tope de voltaje: 1.30 V en Zen 2/3 y 1.20 V en Zen 4/5. Con OC manual no hay "
                                 "límites PPT/TDC/EDC: en placas B350/A320 vigila los VRM. Usa el Asistente para "
                                 "encontrar la frecuencia estable de tu chip.", ["warnbox", "small"], wrap=True))
        right.append(self.oc_panel)

        # Resultado y acciones
        act = panel(None, spacing=10)
        bootrow = Gtk.Box(spacing=10)
        bootrow.append(lbl("Aplicar al iniciar el sistema", hexpand=True))
        self.boot = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.boot.update_property([Gtk.AccessibleProperty.LABEL], ["Aplicar al iniciar el sistema"])
        bootrow.append(self.boot)
        act.append(bootrow)
        act.append(lbl("Apagado: el perfil dura hasta reiniciar. Recomendado mientras pruebas la estabilidad.",
                       ["dim", "small"], wrap=True))
        self.results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        act.append(self.results)
        btns = Gtk.Box(spacing=8)
        rst = Gtk.Button(label="Restablecer de fábrica", hexpand=True)
        rst.connect("clicked", lambda *_: self.win.run_helper(["reset"], None, "Valores de fábrica restaurados"))
        self.apply_btn = Gtk.Button(label="Aplicar", hexpand=True, css_classes=["suggested-action"])
        self.apply_btn.connect("clicked", lambda *_: self._apply())
        btns.append(rst)
        btns.append(self.apply_btn)
        act.append(btns)
        right.append(act)
        self.co_rows: list[Gtk.Adjustment] = []

    # --- configuración según hardware ---
    def setup(self):
        si, st = self.win.si, self.win.status
        lim = st.get("limits") or {}
        feats = lim.get("features", {})
        stock = st.get("stock", {})
        running = self.win.service_running
        rep = self.win.report

        def why(name):
            c = rep.cap(name)
            return c.reason if c else ""

        # Límites
        on = feats.get("limits", False)
        self.lim_panel.set_sensitive(True)
        for r in self.lim_rows:
            r.set_sensitive(on)
        self.r_scalar.set_sensitive(feats.get("scalar", False))
        self.lim_note.set_visible(not on)
        self.lim_note.set_label(f"No disponible: {why('Límites PBO (PPT/TDC/EDC)')}.")
        if lim:
            self.r_ppt.set_range(*lim["ppt"], stock.get("ppt"), " W")
            self.r_tdc.set_range(*lim["tdc"], stock.get("tdc"), " A")
            self.r_edc.set_range(*lim["edc"], stock.get("edc"), " A")
            self.r_tctl.set_range(*lim["tctl"], stock.get("tctl"), " °C")
            self.r_scalar.set_range(1, 10, 1, "×")
        # cpufreq
        f = si.cpufreq
        self.boost.set_sensitive(bool(f.boost_path))
        self.boost.set_active(bool(f.boost))
        self.epp.set_model(Gtk.StringList.new(f.epps or ["—"]))
        self.epp.set_sensitive(bool(f.epps))
        if f.epp in f.epps:
            self.epp.set_selected(f.epps.index(f.epp))
        self.gov.set_model(Gtk.StringList.new(f.governors or ["—"]))
        self.gov.set_sensitive(bool(f.governors))
        if f.governor in f.governors:
            self.gov.set_selected(f.governors.index(f.governor))
        if f.hw_min_khz and f.hw_max_khz:
            for s in (self.fmin, self.fmax):
                s.set_range(f.hw_min_khz // 1000, f.hw_max_khz // 1000)
            self.fmin.set_value((f.min_khz or f.hw_min_khz) // 1000)
            self.fmax.set_value((f.max_khz or f.hw_max_khz) // 1000)
        notes = []
        if not f.epps:
            notes.append("EPP necesita amd-pstate en modo activo")
        if not running:
            notes.append("los cambios necesitan el servicio activo")
        self.freq_note.set_label(" · ".join(notes).capitalize())
        self.freq_note.set_visible(bool(notes))
        # CO
        co_ok = feats.get("co", False)
        rng = lim.get("co") or [-30, 30]
        self.co_all_adj.set_lower(rng[0])
        self.co_all_adj.set_upper(rng[1])
        self.co_btns["all"].set_sensitive(co_ok)
        self.co_btns["per_core"].set_sensitive(feats.get("co_per_core", False))
        if co_ok and not feats.get("co_per_core"):
            self.co_note.set_label("Por núcleo no disponible: el mapa de núcleos no se pudo verificar.")
        else:
            self.co_note.set_label(f"No disponible: {why('Curve Optimizer')}." if not co_ok else "")
        self.co_note.set_visible(bool(self.co_note.get_label()))
        clear(self.co_cores_box)
        self.co_rows = []
        bios = st.get("co_current") or st.get("stock", {}).get("co") or []
        stock_co = st.get("stock", {}).get("co") or []
        best = min((c.rank for c in si.cores if c.rank), default=None)
        grid = Gtk.Grid(column_spacing=12, row_spacing=6, column_homogeneous=True)
        cols = 2 if len(si.cores) > 8 else 1
        for i, c in enumerate(si.cores):
            row = Gtk.Box(spacing=10, css_classes=["co-row"])
            name = f"Núcleo {i}" + (" ★" if best and c.rank == best else "")
            row.append(lbl(name, ["mono"], width_chars=9 if cols == 2 else 10))
            adj = Gtk.Adjustment(lower=rng[0], upper=rng[1], step_increment=1, page_increment=5,
                                 value=bios[i] if i < len(bios) else 0)
            sc = Gtk.Scale(adjustment=adj, draw_value=False, hexpand=True, digits=0)
            sc.set_size_request(110 if cols == 2 else 160, -1)
            sc.update_property([Gtk.AccessibleProperty.LABEL], [f"Curve Optimizer núcleo {i}"])
            row.append(sc)
            sp = Gtk.SpinButton(adjustment=adj, numeric=True, width_chars=3)
            sp.update_property([Gtk.AccessibleProperty.LABEL], [f"Curve Optimizer núcleo {i}"])
            row.append(sp)
            if i < len(stock_co):
                bl = lbl(f"{stock_co[i]:+d}" if cols == 2 else f"BIOS {stock_co[i]:+d}", ["dim", "small", "mono"])
                bl.set_tooltip_text(f"Valor de la BIOS: {stock_co[i]:+d}")
                row.append(bl)
            half = (len(si.cores) + 1) // 2
            grid.attach(row, i // half if cols == 2 else 0, i % half if cols == 2 else i, 1, 1)
            self.co_rows.append(adj)
        self.co_cores_box.append(grid)
        # OC manual
        oc_ok = feats.get("oc", False)
        if lim.get("oc_mhz"):
            self.r_ocf.set_range(*lim["oc_mhz"], (f.hw_max_khz or 0) // 1000 or None, " MHz")
            self.r_ocf.stock.set_label(f"boost máx. {(f.hw_max_khz or 0) // 1000} MHz")
            self.r_ocv.set_range(*lim["oc_volts"])
            self.r_ocv.stock.set_label(f"máx. permitido {lim['oc_volts'][1]:.2f} V")
        self.oc_note.set_label(f"No disponible: {why('OC manual (frecuencia y voltaje fijos)')}.")
        self.oc_note.set_visible(not oc_ok)
        for r in (self.r_ocf, self.r_ocv):
            r.set_sensitive(oc_ok)
        self.mode_btns["manual"].set_sensitive(oc_ok)
        for k in ("eco", "pbo"):
            self.mode_btns[k].set_sensitive(on or bool(f.epps) or bool(f.boost_path))
        self.apply_btn.set_sensitive(True)
        self._build_presets(si, st, lim, feats, stock)

        # Perfil activo: el que está aplicado (sesión o arranque) se carga en los controles
        active = st.get("active") or {}
        self._refresh_profiles()
        if not any(b.get_active() for b in self.mode_btns.values()):
            applied = None
            for path in (SESSION_FILE, BOOT_FILE):
                try:
                    with open(P(path)) as fh:
                        applied = Profile.from_dict(json.load(fh).get("profile", {}))
                    self.boot.set_active(path == BOOT_FILE)
                    break
                except (OSError, ValueError, AttributeError):
                    continue
            if applied:
                self.load(applied)
            else:
                mode = active.get("mode") or "pbo"
                self.mode_btns.get(mode, self.mode_btns["pbo"]).set_active(True)
        self.show_results(active.get("results") or [], active.get("name"))

    # --- perfiles recomendados ---
    NEED_TEXT: ClassVar = {"limits": "Necesita ryzen_smu", "co": "Necesita ryzen_smu", "boost": "Sin control de boost"}

    def _build_presets(self, si, st, lim, feats, stock):
        clear(self.pre_box)
        p = si.platform
        spec = presets.spec_for(si.model_name, p.generation if p else None, system.chipset(si.board_name))
        co_range = (lim.get("co") or [0, 0])[1]
        if not feats:
            # Servicio detenido: aun así mostramos los perfiles de este modelo como referencia
            feats = {}
        items = presets.build(spec, stock if feats else {}, feats, si.cores, co_range or (30 if p and p.generation
                              in ("Zen 3", "Zen 3+") else 50 if p and p.generation.startswith(("Zen 4", "Zen 5"))
                              else 0), si.cpufreq.epps,
                              oc_cap=cpudb.max_manual_voltage(p.generation if p else None))
        model = f"Ryzen {spec.model}" if spec.model else si.model_name
        self.pre_title.set_label(f"Perfiles para tu {model}")
        if spec.known and spec.stock:
            src = stock.get("source") if feats else None
            base = (f"Límites de fábrica leídos del procesador ({src})" if src == "tabla PM" else
                    f"Límites oficiales de AMD para {spec.tdp} W")
            self.pre_sub.set_label(f"{base}: PPT {spec.stock[0]} W · TDC {spec.stock[1]} A · EDC {spec.stock[2]} A · "
                                   f"Tjmax {spec.tjmax} °C · boost {spec.boost_ghz} GHz. Tope de los perfiles: "
                                   f"PPT {spec.cap[0]} W ({spec.cap_reason}). " + " ".join(spec.notes))
        else:
            default_note = "Perfiles calculados a partir de los valores del procesador."
            self.pre_sub.set_label(" ".join(spec.notes) or default_note)
        self._presets = items
        for pr in items:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                           css_classes=["preset"] + (["rec"] if pr.id == "eficiente" else []))
            top = Gtk.Box(spacing=8)
            top.append(lbl(pr.name, ["preset-name"], hexpand=True, wrap=True))
            top.append(lbl(f"riesgo {pr.risk}", ["pill", "ok" if pr.risk == "bajo" else "warn"]))
            card.append(top)
            card.append(lbl(pr.summary, ["mono", "small"], wrap=True))
            card.append(lbl(pr.detail, ["dim", "small"], wrap=True, vexpand=True, yalign=0))
            missing = [n for n in pr.needs if not feats.get(n)]
            btn = Gtk.Button(label="Cargar" if not missing else self.NEED_TEXT.get(missing[0], "No disponible"))
            btn.set_sensitive(not missing)
            btn.connect("clicked", lambda _b, x=pr: self._load_preset(x))
            card.append(btn)
            self.pre_box.append(card)

    def _load_preset(self, pr):
        if pr.values.get("mode") == "stock":
            self.mode_btns["stock"].set_active(True)
            self.win.toast("Perfil «Fábrica» listo: pulsa «Restablecer y aplicar»")
            return
        prof = Profile.from_dict({"name": pr.name, **pr.values})
        if prof.co_mode == "per_core" and len(prof.co_cores) != len(self.co_rows):
            prof.co_mode = "off"
        self.load(prof)
        self.win.toast(f"Perfil «{pr.name}» cargado: revisa los valores y pulsa «Aplicar»")

    # --- modos y perfiles ---
    def _mode(self) -> str:
        return next((k for k, b in self.mode_btns.items() if b.get_active()), "pbo")

    def _mode_toggled(self, btn, key):
        if not btn.get_active():
            return
        self.hint.set_label(self.HINTS[key])
        self.lim_panel.set_visible(key in ("eco", "pbo"))
        self.co_panel.set_visible(key == "pbo")
        self.oc_panel.set_visible(key == "manual")
        self.freq_panel.set_visible(key != "stock")
        if self._loading:
            return
        st = self.win.status
        lim, stock = st.get("limits") or {}, st.get("stock", {})
        feats = lim.get("features", {}) if lim else {}
        wanted = {"eco": ("eco", "silencioso", "ahorro"), "pbo": ("eficiente", "co")}.get(key, ())
        match = next((pr for w in wanted for pr in getattr(self, "_presets", [])
                      if pr.id == w and all(feats.get(n) for n in pr.needs)), None)
        if match:
            p = Profile.from_dict({"name": match.name, **match.values})
            p.mode = key
            if p.co_mode == "per_core" and len(p.co_cores) != len(self.co_rows):
                p.co_mode = "off"
            self.load(p, keep_mode=True)
        elif lim and key != "stock":
            p = preset(key, lim, stock, len(self.win.si.cores))
            current = st.get("co_current") or stock.get("co")
            if p.co_mode == "per_core" and current and len(current) == len(p.co_cores):
                p.co_cores = list(current)   # partir de lo que hay ahora, no de cero
            self.load(p, keep_mode=True)
        self.apply_btn.set_label("Restablecer y aplicar" if key == "stock" else "Aplicar")

    def _co_toggled(self, btn):
        if btn.get_active():
            key = next(k for k, b in self.co_btns.items() if b is btn)
            self.co_stack.set_visible_child_name(key)

    def load(self, p: Profile, keep_mode=False):
        self._loading = True
        stock = self.win.status.get("stock", {})
        if not keep_mode:
            self.mode_btns.get(p.mode, self.mode_btns["pbo"]).set_active(True)
        self.r_ppt.put(p.ppt, stock.get("ppt"))
        self.r_tdc.put(p.tdc, stock.get("tdc"))
        self.r_edc.put(p.edc, stock.get("edc"))
        self.r_tctl.put(p.tctl, stock.get("tctl"))
        self.r_scalar.put(p.scalar, 1)
        self.co_btns.get(p.co_mode, self.co_btns["off"]).set_active(True)
        self.co_all_adj.set_value(p.co_all)
        for adj, v in zip(self.co_rows, p.co_cores or [], strict=False):
            adj.set_value(v)
        if p.oc_mhz:
            self.r_ocf.adj.set_value(p.oc_mhz)
        if p.oc_volts:
            self.r_ocv.adj.set_value(p.oc_volts)
        f = self.win.si.cpufreq
        if p.boost is not None:
            self.boost.set_active(p.boost)
        if p.epp and p.epp in f.epps:
            self.epp.set_selected(f.epps.index(p.epp))
        if p.governor and p.governor in f.governors:
            self.gov.set_selected(f.governors.index(p.governor))
        if p.min_mhz:
            self.fmin.set_value(p.min_mhz)
        if p.max_mhz:
            self.fmax.set_value(p.max_mhz)
        self._loading = False

    def current(self, name=None) -> Profile:
        mode = self._mode()
        si = self.win.si
        f = si.cpufreq
        sel = self.prof_dd.get_selected() - 1
        p = Profile(name=name or (self.profiles[sel].name if 0 <= sel < len(self.profiles) else
                                  dict(self.MODES)[mode]), mode=mode)
        feats = (self.win.status.get("limits") or {}).get("features", {})
        if mode in ("eco", "pbo") and feats.get("limits"):
            p.ppt = self.r_ppt.get()
            p.tdc = self.r_tdc.get()
            p.edc = self.r_edc.get()
            t = self.r_tctl.get()
            p.tctl = int(t) if t is not None else None
            s = self.r_scalar.get() if feats.get("scalar") else None
            p.scalar = int(s) if s is not None else None
            p.ppt, p.tdc, p.edc = [round(v) if v is not None else None for v in (p.ppt, p.tdc, p.edc)]
        if mode == "pbo":
            p.co_mode = next(k for k, b in self.co_btns.items() if b.get_active())
            p.co_all = int(self.co_all_adj.get_value())
            p.co_cores = [int(a.get_value()) for a in self.co_rows]
        if mode == "manual":
            p.oc_mhz = int(self.r_ocf.adj.get_value())
            p.oc_volts = round(self.r_ocv.adj.get_value(), 4)
        if mode != "stock":
            if f.boost_path:
                p.boost = self.boost.get_active()
            if f.epps:
                p.epp = f.epps[self.epp.get_selected()]
            if f.governors:
                p.governor = f.governors[self.gov.get_selected()]
            if f.hw_max_khz:
                p.min_mhz, p.max_mhz = int(self.fmin.get_value()), int(self.fmax.get_value())
                if p.min_mhz == f.hw_min_khz // 1000 and p.max_mhz == f.hw_max_khz // 1000:
                    p.min_mhz = p.max_mhz = None
        return p

    def _refresh_profiles(self, select=None):
        self._loading = True
        self.prof_model.splice(0, self.prof_model.get_n_items(),
                               ["Perfil sin guardar"] + [p.name for p in self.profiles])
        self.prof_dd.set_selected(select + 1 if select is not None else 0)
        self._loading = False

    def _profile_selected(self, dd, _):
        if self._loading:
            return
        i = dd.get_selected() - 1
        if 0 <= i < len(self.profiles):
            self.load(self.profiles[i])

    def _save_as(self, *_):
        dlg = Adw.AlertDialog(heading="Guardar perfil", body="Nombre del perfil:")
        entry = Gtk.Entry(text=self.current().name, activates_default=True)
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", "Cancelar")
        dlg.add_response("save", "Guardar")
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("save")

        def done(d, resp):
            name = entry.get_text().strip()[:40]
            if resp != "save" or not name:
                return
            prof = self.current(name)
            idx = next((i for i, p in enumerate(self.profiles) if p.name == name), None)
            if idx is None:
                self.profiles.append(prof)
                idx = len(self.profiles) - 1
            else:
                self.profiles[idx] = prof
            save_user_profiles(self.profiles)
            self._refresh_profiles(idx)
            self.win.toast(f"Perfil «{name}» guardado")
        dlg.connect("response", done)
        dlg.present(self.win)

    def _delete(self, *_):
        i = self.prof_dd.get_selected() - 1
        if not (0 <= i < len(self.profiles)):
            self.win.toast("Elige un perfil guardado para eliminarlo")
            return
        name = self.profiles.pop(i).name
        save_user_profiles(self.profiles)
        self._refresh_profiles()
        self.win.toast(f"Perfil «{name}» eliminado")

    # --- aplicar ---
    def _apply(self):
        p = self.current()
        if p.mode == "stock":
            self.win.run_helper(["reset"], None, "Valores de fábrica restaurados")
            return
        lim = self.win.status.get("limits") or {}
        if not lim:
            self.win.toast("Activa el servicio cpu-control para aplicar cambios (ver Diagnóstico)")
            return
        try:
            validate(p, lim)
        except ValidationError as e:
            self.win.toast(str(e))
            return
        warn = []
        if p.mode == "manual":
            warn.append(f"OC manual a {p.oc_mhz} MHz y {p.oc_volts:.3f} V: el boost automático se desactiva.")
        if (p.co_mode == "per_core" and min(p.co_cores or [0]) < -20) or (p.co_mode == "all" and p.co_all < -20):
            warn.append("Un Curve Optimizer por debajo de −20 suele ser inestable.")
        payload = json.dumps({"profile": p.to_dict(), "boot": self.boot.get_active()})

        def go():
            self.win.run_helper(["apply"], payload, f"Perfil «{p.name}» aplicado")
        if warn:
            dlg = Adw.AlertDialog(heading="¿Aplicar estos ajustes?", body="\n".join(warn) +
                                  ("\n\n«Aplicar al iniciar» está activado: si el equipo se cuelga, al reiniciar "
                                   "se volverá a aplicar." if self.boot.get_active() else
                                   "\n\nSi el equipo se cuelga, reinicia: los cambios no son permanentes."))
            dlg.add_response("cancel", "Cancelar")
            dlg.add_response("ok", "Aplicar")
            dlg.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
            dlg.connect("response", lambda d, r: go() if r == "ok" else None)
            dlg.present(self.win)
        else:
            go()

    def show_results(self, results, name=None):
        clear(self.results)
        if not results:
            return
        self.results.append(lbl(f"Último resultado{f' — {name}' if name else ''}:", ["dim", "small"]))
        for r in results:
            row = Gtk.Box(spacing=8)
            row.append(lbl("✓" if r.get("ok") else "✗", ["result-ok" if r.get("ok") else "result-err", "mono"]))
            row.append(lbl(f"{r.get('item')}: {r.get('msg')}", ["small"], wrap=True, hexpand=True,
                           selectable=True))
            self.results.append(row)


# --- Página: Diagnóstico ----------------------------------------------------
class DiagPage(Gtk.Box):
    def __init__(self, win: MainWindow):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.root = page_box()
        self.append(scrolled(self.root))

    def build(self):
        clear(self.root)
        rep = self.win.report
        head = Gtk.Box(spacing=10, css_classes=["panel"])
        worst = rep.worst
        icon = Gtk.Image.new_from_icon_name(ICONS["ok" if worst == "ok" else worst])
        icon.add_css_class(f"icon-{'ok' if worst == 'ok' else worst}")
        icon.set_pixel_size(28)
        head.append(icon)
        n_warn = sum(c.level in ("warn", "error") for c in rep.checks)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        col.append(lbl("Todo listo" if not n_warn else f"{n_warn} aviso{'s' if n_warn > 1 else ''} por revisar",
                       ["section-title"]))
        col.append(lbl("Revisa qué falta instalar y qué permite tu placa. Las funciones que no estén disponibles "
                       "aparecen desactivadas en Ajuste.", ["dim", "small"], wrap=True))
        head.append(col)
        if not self.win.service_running:
            start = Gtk.Button(label="Activar servicio", css_classes=["suggested-action"], valign=Gtk.Align.CENTER)
            start.connect("clicked", lambda *_: self.win.run_helper(["start"], None, "Servicio activado"))
            head.append(start)
        again = Gtk.Button(label="Volver a comprobar", valign=Gtk.Align.CENTER)
        again.connect("clicked", lambda *_: self.win.refresh_all(redetect=True))
        head.append(again)
        self.root.append(head)

        caps = panel("Qué funciona en tu equipo", spacing=6)
        grid = Gtk.Grid(column_spacing=24, row_spacing=6, column_homogeneous=True)
        for i, c in enumerate(rep.caps):
            row = Gtk.Box(spacing=8)
            lv = "ok" if c.available and not c.partial else ("warn" if c.available else "error")
            ic = Gtk.Image.new_from_icon_name(ICONS[lv])
            ic.add_css_class(f"icon-{lv}")
            row.append(ic)
            row.append(lbl(c.name, hexpand=True))
            row.append(lbl(c.reason, ["dim", "small"], xalign=1, wrap=True, max_width_chars=34))
            grid.attach(row, i % 2, i // 2, 1, 1)
        caps.append(grid)
        self.root.append(caps)

        groups: dict[str, list] = {}
        for c in rep.checks:
            groups.setdefault(c.group, []).append(c)
        for gname, checks in groups.items():
            p = panel(gname, spacing=0)
            for c in checks:
                row = Gtk.Box(spacing=12, css_classes=["check-row"])
                ic = Gtk.Image.new_from_icon_name(ICONS[c.level])
                ic.add_css_class(f"icon-{c.level}")
                ic.set_valign(Gtk.Align.START)
                ic.set_margin_top(2)
                row.append(ic)
                col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
                col.append(lbl(c.title, wrap=True, selectable=True))
                col.append(lbl(c.detail, ["dim", "small"], wrap=True, selectable=True))
                if c.fix:
                    fx = Gtk.Box(spacing=8)
                    code = lbl(c.fix, ["fix"], hexpand=True, selectable=True, wrap=True)
                    fx.append(code)
                    cp = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copiar", valign=Gtk.Align.START)
                    cp.update_property([Gtk.AccessibleProperty.LABEL], ["Copiar comando"])
                    cp.connect("clicked", lambda _b, t=c.fix: self.win.copy(t))
                    fx.append(cp)
                    col.append(fx)
                row.append(col)
                p.append(row)
            self.root.append(p)

        exp = panel("Funciones experimentales", spacing=8)
        r = Gtk.Box(spacing=10)
        r.append(lbl("Usar comandos del SMU sin validación pública para este procesador (Zen/Zen+, Raven, "
                     "Picasso, Rembrandt, OC en Zen 4/5).", ["small"], wrap=True, hexpand=True))
        sw = Gtk.Switch(valign=Gtk.Align.CENTER, active=bool(self.win.status.get("experimental")))
        sw.update_property([Gtk.AccessibleProperty.LABEL], ["Funciones experimentales"])
        sw.set_sensitive(self.win.service_running)
        sw.connect("state-set", self._exp)
        r.append(sw)
        exp.append(r)
        self.root.append(exp)

    def _exp(self, sw, state):
        self.win.run_helper(["experimental", "on" if state else "off"], None,
                            "Funciones experimentales " + ("activadas" if state else "desactivadas"))
        return False


# --- Página: Asistente de OC manual ---------------------------------------------
class AssistPage(Gtk.Box):
    DURATIONS = (("1 minuto (rápida)", 60), ("3 minutos", 180), ("10 minutos (recomendada al final)", 600))

    def __init__(self, win: MainWindow):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.search: stability.Search | None = None
        self.proc = None
        self.watch = None
        self.t_start = 0.0
        self.max_temp = None
        self.cur_mhz = None
        self.stopping = False
        root = page_box()
        self.append(scrolled(root))

        intro = panel("Asistente de OC manual con tope de voltaje", spacing=8)
        intro.append(lbl("Basado en la guía de michaelnager (r/ryzen): se fija un voltaje máximo seguro y se sube la "
                         "frecuencia de todos los núcleos de 25 en 25 MHz hasta encontrar la más alta estable. "
                         "El resultado suele dar más rendimiento multinúcleo y menos temperatura que el boost de "
                         "fábrica, a cambio de algo de rendimiento en un solo núcleo.", ["small"], wrap=True))
        intro.append(lbl("Diferencias con la guía: cada paso se prueba con stress-ng verificando los resultados "
                         "(no solo Cinebench), se corta si la temperatura llega al tope, y si el equipo se cuelga "
                         "basta con reiniciar: nada se aplica al arranque y el asistente retoma desde la última "
                         "frecuencia estable.", ["dim", "small"], wrap=True))
        self.req = lbl("", ["unavailable"], wrap=True)
        intro.append(self.req)
        root.append(intro)

        cols = Gtk.Box(spacing=16)
        root.append(cols)
        bios = panel("1 · Antes, en la BIOS", spacing=6)
        bios.set_size_request(420, -1)
        self.bios_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bios.append(self.bios_box)
        bios.append(lbl("La app no puede cambiar la BIOS; marca cada paso cuando lo hayas hecho.",
                        ["dim", "small"], wrap=True))
        cols.append(bios)

        cfg = panel("2 · Parámetros", spacing=10)
        cfg.set_hexpand(True)
        r = Gtk.Box(spacing=10)
        r.append(lbl("Voltaje máximo", hexpand=True))
        self.volt_dd = Gtk.DropDown(model=Gtk.StringList())
        self.volt_dd.update_property([Gtk.AccessibleProperty.LABEL], ["Voltaje máximo"])
        r.append(self.volt_dd)
        cfg.append(r)
        self.volt_note = lbl("", ["dim", "small"], wrap=True)
        cfg.append(self.volt_note)
        r = Gtk.Box(spacing=10)
        r.append(lbl("Frecuencia inicial", hexpand=True))
        self.start = Gtk.SpinButton.new_with_range(2500, 6000, 25)
        self.start.update_property([Gtk.AccessibleProperty.LABEL], ["Frecuencia inicial"])
        r.append(self.start)
        r.append(lbl("MHz", ["dim"]))
        cfg.append(r)
        r = Gtk.Box(spacing=10)
        r.append(lbl("Duración de cada prueba", hexpand=True))
        self.dur_dd = Gtk.DropDown(model=Gtk.StringList.new([d[0] for d in self.DURATIONS]))
        self.dur_dd.update_property([Gtk.AccessibleProperty.LABEL], ["Duración de cada prueba"])
        r.append(self.dur_dd)
        cfg.append(r)
        r = Gtk.Box(spacing=10)
        r.append(lbl("Cortar si la temperatura llega a", hexpand=True))
        self.temp = Gtk.SpinButton.new_with_range(60, 95, 1)
        self.temp.update_property([Gtk.AccessibleProperty.LABEL], ["Temperatura máxima"])
        r.append(self.temp)
        r.append(lbl("°C", ["dim"]))
        cfg.append(r)
        r = Gtk.Box(spacing=10)
        r.append(lbl("Dejar 25 MHz de margen en el resultado", hexpand=True))
        self.margin = Gtk.Switch(active=True, valign=Gtk.Align.CENTER)
        self.margin.update_property([Gtk.AccessibleProperty.LABEL], ["Margen de seguridad"])
        r.append(self.margin)
        cfg.append(r)
        btns = Gtk.Box(spacing=8)
        self.btn_start = Gtk.Button(label="Empezar", hexpand=True, css_classes=["suggested-action"])
        self.btn_start.connect("clicked", lambda *_: self._start())
        self.btn_stop = Gtk.Button(label="Detener", hexpand=True, sensitive=False)
        self.btn_stop.connect("clicked", lambda *_: self._stop("detenido por ti"))
        self.btn_reset = Gtk.Button(label="Borrar progreso")
        self.btn_reset.connect("clicked", lambda *_: self._clear())
        btns.append(self.btn_start)
        btns.append(self.btn_stop)
        btns.append(self.btn_reset)
        cfg.append(btns)
        cols.append(cfg)

        prog = panel("3 · Progreso", spacing=8)
        self.now = lbl("Sin iniciar.", ["section-title"], wrap=True)
        prog.append(self.now)
        self.bar = Gtk.ProgressBar(css_classes=["bar"])
        prog.append(self.bar)
        self.live = lbl("", ["mono", "small", "dim"])
        prog.append(self.live)
        self.log = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        prog.append(self.log)
        root.append(prog)

        self.res = panel("Resultado", spacing=8)
        self.res_text = lbl("", ["small"], wrap=True)
        self.res.append(self.res_text)
        rb = Gtk.Box(spacing=8)
        self.btn_apply = Gtk.Button(label="Aplicar ahora (hasta reiniciar)", css_classes=["suggested-action"])
        self.btn_apply.connect("clicked", lambda *_: self._apply_result(False))
        self.btn_save = Gtk.Button(label="Guardar como perfil")
        self.btn_save.connect("clicked", lambda *_: self._save_result())
        rb.append(self.btn_apply)
        rb.append(self.btn_save)
        self.res.append(rb)
        self.res.append(lbl("Antes de usarlo a diario, déjalo una hora con una prueba larga (10 min × varias) y "
                            "usa el equipo normal unos días con «Aplicar al iniciar» apagado.",
                            ["dim", "small"], wrap=True))
        self.res.set_visible(False)
        root.append(self.res)

        self.search = stability.Search.load()
        self._render()

    # --- configuración según hardware ---
    def setup(self):
        si, st = self.win.si, self.win.status
        lim = st.get("limits") or {}
        feats = lim.get("features", {})
        gen = si.platform.generation if si.platform else None
        cap = cpudb.max_manual_voltage(gen)
        volts = [round(cap - 0.10, 2), round(cap - 0.05, 2), cap]
        self._volts = volts
        sel = self.volt_dd.get_selected() if hasattr(self, "_volts_set") else 2
        self.volt_dd.set_model(Gtk.StringList.new(
            [f"{v:.2f} V" + (" (máximo de la guía)" if v == cap else " (más eficiente)" if v == volts[0] else "")
             for v in volts]))
        self.volt_dd.set_selected(min(sel, 2))
        self._volts_set = True
        self.volt_note.set_label(f"Tope para {gen or 'este procesador'}: {cap:.2f} V. Con menos voltaje la frecuencia "
                                 "final es menor pero el consumo y la temperatura bajan.")
        spec = presets.spec_for(si.model_name, gen, system.chipset(si.board_name))
        boost = int(spec.boost_ghz * 1000) if spec.boost_ghz else None
        hw_max = (si.cpufreq.hw_max_khz or 0) // 1000 or None
        if not self.search:
            self.start.set_value(stability.default_start(boost, hw_max))
        tj = spec.tjmax or 90
        if self.temp.get_value() in (60, 0):
            self.temp.set_value(tj - 5)
        self.temp.set_range(60, tj)
        clear(self.bios_box)
        msi = "micro-star" in si.board_vendor.lower() or "msi" in si.board_vendor.lower()
        for title, detail in (stability.BIOS_STEPS_MSI if msi else stability.BIOS_STEPS_GENERIC):
            cb = Gtk.CheckButton(label=title)
            self.bios_box.append(cb)
            self.bios_box.append(lbl(detail, ["dim", "small"], wrap=True, margin_start=28))
        problems = []
        if not self.win.service_running:
            problems.append("el servicio cpu-control está detenido")
        if not feats.get("oc"):
            c = self.win.report.cap("OC manual (frecuencia y voltaje fijos)") if self.win.report else None
            problems.append(f"OC manual no disponible ({c.reason if c else 'ver Diagnóstico'})")
        if not stability.stress_command(1):
            problems.append("falta stress-ng: sudo pacman -S stress-ng")
        self.req.set_label("No se puede empezar: " + "; ".join(problems) + "." if problems else "")
        self.req.set_visible(bool(problems))
        self.btn_start.set_sensitive(not problems and self.proc is None)
        self._lim = lim
        self._render()

    # --- flujo ---
    def _start(self):
        if self.search and not self.search.done:
            self._next()
            return
        lim = self._lim
        hi = (lim.get("oc_mhz") or [2000, 5000])[1]
        v = self._volts[self.volt_dd.get_selected()]
        dur = self.DURATIONS[self.dur_dd.get_selected()][1]
        self.search = stability.Search(volts=v, start_mhz=int(self.start.get_value()), max_mhz=hi,
                                       duration_s=dur, temp_limit=int(self.temp.get_value()))
        self.search.save()
        dlg = Adw.AlertDialog(
            heading="¿Empezar el asistente?",
            body=f"Se fijarán {v:.2f} V y {self.search.start_mhz} MHz en todos los núcleos, y se subirá de 25 en "
                 f"25 MHz probando {dur // 60} min cada paso. El equipo puede colgarse o reiniciarse: es normal "
                 "al llegar al límite. Guarda tu trabajo y cierra programas pesados.")
        dlg.add_response("cancel", "Cancelar")
        dlg.add_response("go", "Empezar")
        dlg.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda d, r: self._next() if r == "go" else None)
        dlg.present(self.win)

    def _next(self):
        s = self.search
        mhz = s.next_mhz()
        if mhz is None:
            self._end()
            return
        self.cur_mhz = mhz
        self.stopping = False
        s.begin(mhz)
        self.btn_start.set_sensitive(False)
        self.btn_stop.set_sensitive(True)
        self.now.set_label(f"Aplicando {mhz} MHz a {s.volts:.2f} V…")
        prof = Profile(name="Asistente", mode="manual", oc_mhz=mhz, oc_volts=s.volts)
        payload = json.dumps({"profile": prof.to_dict(), "boot": False})
        self.win.run_helper(["apply"], payload, f"{mhz} MHz aplicado", on_done=self._applied, quiet=True)

    def _applied(self, ok, result):
        if not ok:
            msg = result.get("error") or "; ".join(f"{r['item']}: {r['msg']}" for r in result.get("results", [])
                                                   if not r.get("ok"))
            self.search.pending = None
            self.search.finish(f"No se pudo aplicar el OC manual: {msg}")
            self._idle_buttons()
            self._render()
            return
        if self.stopping:
            return self._restore()
        GLib.timeout_add(1500, self._run_stress)

    def _run_stress(self):
        s = self.search
        cmd = stability.stress_command(s.duration_s)
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        launcher.setenv("CPU_CONTROL_TEST_MHZ", str(self.cur_mhz), True)
        try:
            self.proc = launcher.spawnv(cmd)
        except GLib.Error as e:
            self.search.pending = None
            self.search.finish(f"No se pudo ejecutar stress-ng: {e.message}")
            self._idle_buttons()
            return False
        self.t_start = time.monotonic()
        self.max_temp = None
        self.now.set_label(f"Probando {self.cur_mhz} MHz a {s.volts:.2f} V…")
        self.watch = GLib.timeout_add(1000, self._tick)
        self.proc.communicate_utf8_async(None, None, self._stress_done)
        return False

    def _tick(self):
        s = self.search
        x = self.win.last_sample
        t = x.temp if x else None
        if t is not None:
            self.max_temp = max(self.max_temp or 0, t)
        el = time.monotonic() - self.t_start
        self.bar.set_fraction(min(1.0, el / s.duration_s))
        self.live.set_label(f"{int(el)} / {s.duration_s} s · {fmt(t, ' °C')} (máx. {fmt(self.max_temp, ' °C')}) · "
                            f"{fmt(x.freq_avg if x else None, ' MHz')} · {fmt(x.power if x else None, ' W', 1)}")
        if t is not None and t >= s.temp_limit and self.proc:
            self._fail_reason = f"temperatura {t:.0f} °C ≥ {s.temp_limit} °C"
            self.proc.force_exit()
        return True

    def _stress_done(self, proc, res):
        if self.watch:
            GLib.source_remove(self.watch)
            self.watch = None
        try:
            _, out, _ = proc.communicate_utf8_finish(res)
        except GLib.Error:
            out = ""
        self.proc = None
        secs = round(time.monotonic() - self.t_start)
        reason = getattr(self, "_fail_reason", None)
        self._fail_reason = None
        if self.stopping:
            self.search.pending = None
            self.search.save()
            return self._restore()
        ok = reason is None and proc.get_if_exited() and proc.get_exit_status() == 0
        if not ok and reason is None:
            tail = [ln for ln in (out or "").splitlines() if "fail" in ln.lower() or "error" in ln.lower()]
            reason = (tail[-1].strip()[:120] if tail else f"stress-ng terminó con código {proc.get_exit_status()}")
        self.search.record(self.cur_mhz, ok, "estable" if ok else reason, self.max_temp, secs)
        self._render()
        if ok:
            self._next()
        else:
            self._restore()

    def _stop(self, why):
        self.stopping = True
        self.now.set_label(f"Deteniendo ({why})…")
        if self.proc:
            self.proc.force_exit()
        else:
            self._restore()

    def _restore(self):
        self.now.set_label("Volviendo a los valores de fábrica…")
        self.win.run_helper(["reset"], None, "Valores de fábrica restaurados", on_done=lambda ok, r: self._end(),
                            quiet=True)

    def _end(self):
        self._idle_buttons()
        self.live.set_label("")
        self._render()

    def _idle_buttons(self):
        self.btn_stop.set_sensitive(False)
        self.btn_start.set_sensitive(not self.req.get_visible())
        self.bar.set_fraction(0)

    def _clear(self):
        if self.proc:
            return
        stability.Search.clear()
        self.search = None
        self._render()

    # --- resultado ---
    def _render(self):
        clear(self.log)
        s = self.search
        if not s:
            self.now.set_label("Sin iniciar.")
            self.res.set_visible(False)
            self.btn_start.set_label("Empezar")
            return
        for st in s.steps:
            row = Gtk.Box(spacing=8)
            row.append(lbl("✓" if st["ok"] else "✗", ["result-ok" if st["ok"] else "result-err", "mono"]))
            row.append(lbl(f"{st['mhz']} MHz @ {s.volts:.2f} V", ["mono", "small"], width_chars=20))
            extra = f" · máx. {st['max_temp']:.0f} °C" if st.get("max_temp") else ""
            row.append(lbl(f"{st['detail']}{extra}", ["small", "dim"], wrap=True, hexpand=True))
            self.log.append(row)
        if s.done:
            self.now.set_label(s.reason or "Terminado.")
            rec = s.recommended(self.margin.get_active())
            self.res.set_visible(rec is not None)
            if rec:
                self.res_text.set_markup(f"Frecuencia recomendada: <b>{rec} MHz a {s.volts:.2f} V</b> en todos los "
                                         f"núcleos" + (" (con 25 MHz de margen)." if rec != s.last_good else "."))
            self.btn_start.set_label("Empezar de nuevo")
        else:
            if self.proc is None and s.steps:
                self.now.set_label(f"En pausa. Último estable: {s.last_good or '—'} MHz.")
            self.btn_start.set_label("Continuar" if s.steps else "Empezar")
            self.res.set_visible(False)

    def _result_profile(self, name="OC manual (asistente)"):
        s = self.search
        rec = s.recommended(self.margin.get_active())
        return Profile(name=name, mode="manual", oc_mhz=rec, oc_volts=s.volts)

    def _apply_result(self, boot):
        p = self._result_profile()
        self.win.run_helper(["apply"], json.dumps({"profile": p.to_dict(), "boot": boot}),
                            f"{p.oc_mhz} MHz a {p.oc_volts:.2f} V aplicado")

    def _save_result(self):
        p = self._result_profile(f"Manual {self.search.recommended(self.margin.get_active())} MHz "
                                 f"{self.search.volts:.2f} V")
        tune = self.win.tune
        idx = next((i for i, q in enumerate(tune.profiles) if q.name == p.name), None)
        if idx is None:
            tune.profiles.append(p)
        else:
            tune.profiles[idx] = p
        save_user_profiles(tune.profiles)
        tune._refresh_profiles()
        self.win.toast(f"Perfil «{p.name}» guardado en Ajuste")

    def on_start_restore_banner(self):
        s = self.search
        if s and s.steps and not s.steps[-1]["ok"] and "colgó" in s.steps[-1]["detail"]:
            return (f"La prueba a {s.steps[-1]['mhz']} MHz no terminó (el equipo se colgó o reinició). "
                    f"Último estable: {s.last_good or '—'} MHz.")
        return None


# --- Ventana -------------------------------------------------------------------
class MainWindow(Adw.ApplicationWindow):
    PAGES = (("home", "Inicio", "go-home-symbolic"), ("hist", "Historial", "document-open-recent-symbolic"),
             ("tune", "Ajuste", "preferences-system-symbolic"), ("assist", "Asistente OC", "starred-symbolic"),
             ("diag", "Diagnóstico", "dialog-information-symbolic"))

    def __init__(self, app):
        super().__init__(application=app, title="CPU Control", default_width=1280, default_height=860)
        self.add_css_class("cpu")
        self.si = system.detect()
        self.sampler = Sampler(self.si)
        self.history = History()
        self.status: dict = {}
        self.last_sample = None
        self.service_running = False
        self.report = None
        self.csv = None
        self.t0 = time.monotonic()
        self._status_mtime = 0

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        tbox = Gtk.Box(spacing=10)
        ic = Gtk.Image.new_from_icon_name(APP_ID)
        ic.set_pixel_size(22)
        tbox.append(ic)
        tbox.append(lbl("CPU Control", ["section-title"]))
        hb.set_title_widget(tbox)
        self.cpu_lbl = lbl(self.si.model_name, ["dim"])
        hb.pack_start(self.cpu_lbl)
        self.svc_pill = lbl("", ["pill"])
        hb.pack_end(self.svc_pill)
        self.prof_pill = lbl("", ["pill", "accent"])
        hb.pack_end(self.prof_pill)
        view.add_top_bar(hb)
        self.banner = Adw.Banner(button_label="Ver diagnóstico")
        self._banner_target = "diag"
        self.banner.connect("button-clicked", lambda *_: self.go(self._banner_target))
        view.add_top_bar(self.banner)

        body = Gtk.Box()
        self.side = Gtk.ListBox(css_classes=["sidebar", "navigation-sidebar"])
        self.side.set_size_request(200, -1)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True)
        self.home, self.hist, self.tune, self.diag = HomePage(self), HistoryPage(self), TuningPage(self), DiagPage(self)
        self.assist = AssistPage(self)
        self.rows = {}
        pages = (self.home, self.hist, self.tune, self.assist, self.diag)
        for (key, text, icon), page in zip(self.PAGES, pages, strict=True):
            self.stack.add_named(page, key)
            row = Gtk.ListBoxRow()
            rb = Gtk.Box(spacing=10)
            rb.append(Gtk.Image.new_from_icon_name(icon))
            rb.append(lbl(text, hexpand=True))
            badge = lbl("", ["pill", "warn"])
            badge.set_visible(False)
            rb.append(badge)
            row.set_child(rb)
            row.key, row.badge = key, badge
            self.rows[key] = row
            self.side.append(row)
        self.side.connect("row-selected", lambda lb, r: r and self.stack.set_visible_child_name(r.key))
        body.append(self.side)
        body.append(self.stack)
        view.set_content(body)
        self.statusbar = lbl("", ["statusbar", "mono"])
        view.add_bottom_bar(self.statusbar)
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        self.refresh_all(redetect=False)
        self.side.select_row(self.rows["home"])
        self._tick()
        self._timer = GLib.timeout_add(SAMPLE_MS, self._tick)
        self.connect("close-request", self._close)

    def go(self, key):
        self.side.select_row(self.rows[key])
        if key == "assist" and self._banner_target == "assist":
            self.banner.set_revealed(False)
            self._banner_target = "diag"

    # --- estado ---
    def _read_status(self) -> bool:
        try:
            m = os.path.getmtime(P(STATUS_FILE))
        except OSError:
            m = 0
        if m == self._status_mtime:
            return False
        self._status_mtime = m
        try:
            with open(P(STATUS_FILE)) as f:
                self.status = json.load(f)
        except (OSError, ValueError):
            self.status = {}
        return True

    def refresh_all(self, redetect=False):
        if redetect:
            self.si = system.detect()
            self.sampler = Sampler(self.si)
        self._read_status()
        self.service_running = bool(self.sampler.telemetry())
        self.report = diagnostics.build(self.si, self.status if self.service_running else {}, self.service_running)
        self.home.setup(self.si)
        self.tune.setup()
        self.assist.setup()
        self.diag.build()
        n = sum(c.level in ("warn", "error") for c in self.report.checks)
        self.rows["diag"].badge.set_label(str(n))
        self.rows["diag"].badge.set_visible(n > 0)
        missing = [c.name for c in self.report.caps if not c.available]
        crash = self.assist.on_start_restore_banner()
        if crash and not getattr(self, "_crash_shown", False):
            self._crash_shown = True
            self.banner.set_title(crash)
            self.banner.set_button_label("Ver asistente")
            self._banner_target = "assist"
            self.banner.set_revealed(True)
        elif getattr(self, "_crash_shown", False):
            pass
        elif self.report.worst == "error" or missing:
            self.banner.set_title(f"{len(missing)} función(es) no disponibles en este equipo: "
                                  + ", ".join(m.split(" (")[0] for m in missing[:3])
                                  + ("…" if len(missing) > 3 else ""))
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)
        if redetect:
            self.toast("Comprobación actualizada")

    def _tick(self):
        x = self.sampler.sample()
        self.last_sample = x
        running = bool(self.sampler.telemetry())
        if self._read_status() or running != self.service_running:
            self.refresh_all()
        h = self.history
        h.add("temp", x.t, x.temp)
        h.add("power", x.t, x.power)
        h.add("freq", x.t, x.freq_avg)
        h.add("usage", x.t, x.usage_total)
        h.add("vcore", x.t, x.vcore)
        h.add("curr", x.t, x.tdc[0] if x.tdc and x.tdc[0] is not None else x.icore)
        page = self.stack.get_visible_child_name()
        if page == "home":
            self.home.update(x)
        elif page == "hist":
            self.hist.update()
        if self.csv:
            self._csv_row(x)
        act = (self.status.get("active") or {}).get("name") if self.service_running else None
        self.prof_pill.set_label(f"Perfil: {act}" if act else "Perfil: BIOS")
        self.svc_pill.set_label("servicio activo" if self.service_running else "servicio detenido")
        self.svc_pill.set_css_classes(["pill", "ok" if self.service_running else "warn"])
        up = int(time.monotonic() - self.t0)
        sm = self.status.get("smu", {})
        self.statusbar.set_label(
            f"Wayland · kernel {self.si.kernel} · {self.si.cpufreq.driver or 'sin cpufreq'}"
            f" · SMU {sm.get('version') or '—'} · BIOS {self.si.bios_version}"
            f"{' · ● grabando CSV' if self.csv else ''}"
            f"   ·   sesión {up // 3600:02d}:{up % 3600 // 60:02d}:{up % 60:02d}")
        return True

    # --- CSV ---
    def toggle_csv(self, on):
        if on and not self.csv:
            folder = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS) or GLib.get_home_dir()
            path = os.path.join(folder, f"cpu-control-{dt.datetime.now():%Y%m%d-%H%M%S}.csv")
            try:
                # se queda abierto mientras se graba; se cierra al parar o al salir
                f = open(path, "w", newline="")  # noqa: SIM115
            except OSError as e:
                self.toast(f"No se pudo crear el CSV: {e.strerror}")
                return
            w = csv.writer(f)
            n = len(self.si.cores)
            w.writerow(["fecha", "temp_c", "potencia_w", "reloj_prom_mhz", "uso_pct", "vcore_v", "ppt_w", "tdc_a",
                        "edc_a"] + [f"nucleo{i}_mhz" for i in range(n)])
            self.csv = (f, w)
            self.toast(f"Grabando en {path}")
        elif not on and self.csv:
            self.csv[0].close()
            self.toast(f"CSV guardado: {self.csv[0].name}")
            self.csv = None

    def _csv_row(self, x: Sample):
        def v(p):
            return p[0] if p else ""
        self.csv[1].writerow([dt.datetime.now().isoformat(timespec="milliseconds"), x.temp, x.power, x.freq_avg,
                              x.usage_total, x.vcore, v(x.ppt), v(x.tdc), v(x.edc)] +
                             [round(c.freq_mhz) if c.freq_mhz else "" for c in x.cores])

    # --- helper ---
    def run_helper(self, args, stdin_text, ok_msg, on_done=None, quiet=False):
        """Ejecuta el helper con pkexec. on_done(ok: bool, result: dict) se llama al terminar."""
        def finish(ok, result):
            if on_done:
                on_done(ok, result)

        if MOCK:
            if not quiet:
                self.toast(f"(simulado) {ok_msg}")
            GLib.timeout_add(300, lambda: (finish(True, {"ok": True, "results": []}), False)[1])
            return
        if not shutil.which("pkexec"):
            self.toast("Falta pkexec (paquete polkit)")
            return finish(False, {"error": "falta pkexec"})
        if not os.path.exists(HELPER):
            self.toast("El helper no está instalado; instala el paquete cpu-control")
            return finish(False, {"error": "helper no instalado"})
        flags = Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
        if stdin_text is not None:
            flags |= Gio.SubprocessFlags.STDIN_PIPE
        try:
            proc = Gio.Subprocess.new(["pkexec", HELPER, *args], flags)
        except GLib.Error as e:
            self.toast(f"No se pudo ejecutar pkexec: {e.message}")
            return finish(False, {"error": e.message})
        if not quiet:
            self.toast("Aplicando…")

        def done(p, res):
            try:
                _, out, err = p.communicate_utf8_finish(res)
            except GLib.Error as e:
                self.toast(f"Error: {e.message}")
                return finish(False, {"error": e.message})
            if p.get_exit_status() in (126, 127):
                self.toast("Autenticación cancelada")
                return finish(False, {"error": "autenticación cancelada"})
            try:
                result = json.loads((out or "").strip().splitlines()[-1])
            except (ValueError, IndexError):
                result = {"ok": False, "error": (err or "sin respuesta").strip()}
            ok = bool(result.get("ok"))
            if ok:
                res_list = result.get("results") or []
                bad = [r for r in res_list if not r.get("ok")]
                ok = not bad
                if not quiet:
                    self.toast(ok_msg if not bad else f"{ok_msg} con {len(bad)} aviso(s): revisa el resultado")
                self.tune.show_results(res_list)
            else:
                self.toast(f"No se aplicó: {result.get('error')}")
            finish(ok, result)
            GLib.timeout_add(800, lambda: (self.refresh_all(), False)[1])

        proc.communicate_utf8_async(stdin_text, None, done)

    def copy(self, text):
        self.get_clipboard().set_content(Gdk.ContentProvider.new_for_bytes(
            "text/plain;charset=utf-8", GLib.Bytes.new(text.encode())))
        self.toast("Copiado")

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))

    def _close(self, *_):
        GLib.source_remove(self._timer)
        if self.csv:
            self.csv[0].close()
        return False


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        prov = Gtk.CssProvider()
        prov.load_from_string(CSS + (CSS_ACCENT_VARS if Gtk.get_minor_version() >= 16 else CSS_ACCENT_OLD))
        disp = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(disp, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        if os.path.isdir(here):
            Gtk.IconTheme.get_for_display(disp).add_search_path(here)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            si = system.detect()
            if not si.is_zen:
                win = Adw.ApplicationWindow(application=self, title="CPU Control", default_width=640,
                                            default_height=400)
                tv = Adw.ToolbarView()
                tv.add_top_bar(Adw.HeaderBar())
                tv.set_content(Adw.StatusPage(icon_name="dialog-error-symbolic", title="Procesador no compatible",
                                              description=f"{si.model_name}\n\nCPU Control funciona con procesadores "
                                                          "AMD Ryzen (Zen o posterior)."))
                win.set_content(tv)
            else:
                win = MainWindow(self)
        win.present()


def main(argv=None) -> int:
    if not os.environ.get("WAYLAND_DISPLAY"):
        print("CPU Control requiere una sesión Wayland (WAYLAND_DISPLAY no está definido).", file=sys.stderr)
        return 1
    return App().run(argv or sys.argv)


_ = (__version__, draw_text)
