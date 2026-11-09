"""Widgets dibujados con cairo: medidor circular, gráfica en vivo y editor de curva."""
from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Pango, PangoCairo


def rgb(hexstr: str, a: float = 1.0) -> tuple[float, float, float, float]:
    h = hexstr.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, a)


ACCENT = "#ff7a45"
GREEN = "#3ddc97"
ORANGE = "#ffb454"
BLUE = "#6aa8ff"
PINK = "#f472b6"
VIOLET = "#a78bfa"
RED = "#f87171"
GRID = rgb("#252b34")
MUTED = rgb("#8b95a3")
TEXT = rgb("#e8ecf1")
BG_DEEP = rgb("#0e1014")


def draw_text(cr, x, y, text, size=8.5, color=MUTED, align="left", valign="center", bold=False):
    layout = PangoCairo.create_layout(cr)
    fd = Pango.FontDescription.from_string(f"JetBrains Mono,DejaVu Sans Mono,monospace {size}")
    if bold:
        fd.set_weight(Pango.Weight.SEMIBOLD)
    layout.set_font_description(fd)
    layout.set_text(text, -1)
    w, h = layout.get_pixel_size()
    if align == "right":
        x -= w
    elif align == "center":
        x -= w / 2
    if valign == "center":
        y -= h / 2
    elif valign == "bottom":
        y -= h
    cr.set_source_rgba(*color)
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, layout)


class RingGauge(Gtk.Box):
    """Anillo con el valor al centro y título/subtítulo a la derecha."""

    def __init__(self, title: str, color: str):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.add_css_class("tile")
        self.color = rgb(color)
        self.frac: float | None = None

        overlay = Gtk.Overlay()
        self.area = Gtk.DrawingArea(content_width=76, content_height=76)
        self.area.set_draw_func(self._draw)
        overlay.set_child(self.area)
        self.value = Gtk.Label(label="—", css_classes=["ring-value"])
        overlay.add_overlay(self.value)
        self.append(overlay)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        col.append(Gtk.Label(label=title, xalign=0, css_classes=["tile-title"]))
        self.sub = Gtk.Label(label="", xalign=0, css_classes=["tile-sub"], ellipsize=3, max_width_chars=16)
        col.append(self.sub)
        self.append(col)
        self.update_property([Gtk.AccessibleProperty.LABEL], [title])

    def set(self, frac: float | None, text: str, sub: str = "") -> None:
        self.frac = None if frac is None else max(0.0, min(1.0, frac))
        self.value.set_label(text)
        self.sub.set_label(sub)
        self.area.queue_draw()

    def _draw(self, area, cr, w, h):
        r = min(w, h) / 2 - 6
        cx, cy = w / 2, h / 2
        cr.set_line_width(8)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_source_rgba(*GRID)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.stroke()
        if self.frac:
            cr.set_source_rgba(*self.color)
            start = -math.pi / 2
            cr.arc(cx, cy, r, start, start + 2 * math.pi * self.frac)
            cr.stroke()


class LineChart(Gtk.DrawingArea):
    """Gráfica de una serie en una ventana de tiempo, con línea de límite opcional."""

    def __init__(self, color: str, unit: str, fixed_max: float | None = None, height: int = 130):
        super().__init__(content_height=height, hexpand=True)
        self.color = rgb(color)
        self.fill = rgb(color, 0.10)
        self.unit = unit
        self.fixed_max = fixed_max
        self.points: list[tuple[float, float]] = []
        self.now = 0.0
        self.window = 300.0
        self.limit: float | None = None
        self.limit_label = ""
        self.set_draw_func(self._draw)

    def set_data(self, points, now: float, window: float, limit: float | None = None,
                 limit_label: str = "") -> None:
        self.points, self.now, self.window = points, now, window
        self.limit, self.limit_label = limit, limit_label
        self.queue_draw()

    @staticmethod
    def _nice_ceil(v: float) -> float:
        if v <= 0:
            return 1
        exp = 10 ** math.floor(math.log10(v))
        for m in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
            if v <= m * exp:
                return m * exp
        return 10 * exp

    def _draw(self, area, cr, w, h):
        left, right, top, bottom = 44, 8, 8, 8
        pw, ph = w - left - right, h - top - bottom
        values = [v for t, v in self.points if t >= self.now - self.window]
        ymax = self.fixed_max or self._nice_ceil(max([*values, self.limit or 0, 1]) * 1.1)

        cr.set_line_width(1)
        for i in range(5):
            y = top + ph * i / 4
            cr.set_source_rgba(*GRID)
            cr.move_to(left, y + 0.5)
            cr.line_to(w - right, y + 0.5)
            cr.stroke()
            val = ymax * (1 - i / 4)
            draw_text(cr, left - 6, y, f"{val:.2f}" if ymax <= 5 else f"{val:.0f}", align="right")

        def xy(t, v):
            return (left + (t - (self.now - self.window)) / self.window * pw,
                    top + ph * (1 - min(v, ymax) / ymax))

        if self.limit:
            _, ly = xy(self.now, self.limit)
            cr.set_source_rgba(*rgb(RED, 0.75))
            cr.set_dash([6, 5])
            cr.move_to(left, ly)
            cr.line_to(w - right, ly)
            cr.stroke()
            cr.set_dash([])
            if self.limit_label:
                draw_text(cr, w - right - 4, ly - 8, self.limit_label, color=rgb(RED, 0.9), align="right")

        pts = [xy(t, v) for t, v in self.points if t >= self.now - self.window - 2]
        if len(pts) < 2:
            draw_text(cr, left + pw / 2, top + ph / 2, "Recolectando datos…", align="center")
            return
        cr.save()
        cr.rectangle(left, top, pw, ph)
        cr.clip()
        cr.move_to(pts[0][0], top + ph)
        for x, y in pts:
            cr.line_to(x, y)
        cr.line_to(pts[-1][0], top + ph)
        cr.close_path()
        cr.set_source_rgba(*self.fill)
        cr.fill()
        cr.set_line_width(2)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        cr.set_source_rgba(*self.color)
        cr.stroke()
        cr.restore()
