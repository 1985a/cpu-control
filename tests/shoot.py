"""Arranca la interfaz con un equipo simulado y guarda capturas de cada página.

python3 tests/shoot.py <salida> <5500|5950x|7800x3d> [--smu] [--page-prefix X]
Requiere un compositor Wayland (p. ej. weston --backend=headless).
"""
import os
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT, KEY = sys.argv[1], sys.argv[2]
SMU = "--smu" in sys.argv
PREFIX = sys.argv[sys.argv.index("--page-prefix") + 1] if "--page-prefix" in sys.argv else KEY

root = tempfile.mkdtemp(prefix="cpuc-shot-")
os.environ["CPU_CONTROL_SYSROOT"] = root
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="cpuc-cfg-")
# stress simulado: estable hasta 4150 MHz
os.environ["CPU_CONTROL_FAKE_STRESS"] = 'sleep 1; [ "$CPU_CONTROL_TEST_MHZ" -le 4150 ]'
ASSIST = "--assist" in sys.argv
import fake

fake.build(root, KEY, smu=SMU)
sys.path.insert(0, os.path.dirname(HERE))
from cpucontrol import daemon as dmod

fs = fake.FakeSmu(KEY) if SMU else None
if SMU:
    dmod.Smu = lambda: fs
else:
    from cpucontrol.smu import SmuError

    def nosmu():
        raise SmuError("ryzen_smu no está cargado")
    dmod.Smu = nosmu
d = dmod.Daemon()
if SMU:
    from cpucontrol.profile import Profile
    prof = Profile(name="PBO diario", mode="pbo", ppt=105, tdc=70, edc=100, co_mode="per_core",
                   co_cores=[-15, -10, -20, -15, -10, -20, -15, -15, -10, -10, -15, -15, -20, -10, -15, -10][:len(d.si.cores)],
                   boost=True, epp="balance_performance", governor="powersave")
    d.apply(prof)
    dmod._save(dmod.SESSION_FILE, {"profile": prof.to_dict()})
    d.active = {"name": "PBO diario", "mode": "pbo", "source": "session", "time": 1, "results": [
        {"item": "PPT", "ok": True, "msg": "aplicado"}, {"item": "TDC", "ok": True, "msg": "aplicado"},
        {"item": "EDC", "ok": True, "msg": "aplicado"},
        {"item": "Curve Optimizer por núcleo", "ok": True, "msg": "−15, −10, −20, −15, −10, −20"}]}
    d.co_probe, d.co_current = d._probe_co()
dmod._save(dmod.STATUS_FILE, d.status())
stop = threading.Event()
threading.Thread(target=fake.live, args=(root, KEY, fs, d, stop), daemon=True).start()

from cpucontrol import app as A
from gi.repository import GLib, Gtk

pages = ["home", "hist", "tune", "assist", "diag"]


def snap(win, name):
    w, h = win.get_width(), win.get_height()
    paint = Gtk.WidgetPaintable.new(win)
    s = Gtk.Snapshot()
    paint.snapshot(s, w, h)
    tex = win.get_native().get_renderer().render_texture(s.to_node(), None)
    tex.save_to_png(os.path.join(OUT, f"{PREFIX}-{name}.png"))


def run(app):
    win = app.props.active_window
    st = {"i": 0}
    if ASSIST:
        from cpucontrol import stability
        win.assist.search = stability.Search(volts=1.30, start_mhz=4075, max_mhz=4500, duration_s=60, temp_limit=85)
        win.assist._next()

    def step():
        i = st["i"]
        if i > 0:
            snap(win, pages[i - 1])
        if i == len(pages):
            stop.set()
            app.quit()
            return False
        win.go(pages[i])
        st["i"] += 1
        return True
    GLib.timeout_add(6000, lambda: (GLib.timeout_add(2000, step), False)[1])


a = A.App()
a.connect("activate", lambda x: GLib.idle_add(run, x))
a.run([])
