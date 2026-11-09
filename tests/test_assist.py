"""Lógica del asistente: búsqueda de 25 en 25, fallo, recuperación tras cuelgue y margen."""
import os
import sys
import tempfile

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cpucontrol import cpudb, stability

LIMIT = 4325  # el chip simulado es estable hasta aquí

s = stability.Search(volts=1.30, start_mhz=4000, max_mhz=4600)
while (m := s.next_mhz()) is not None:
    s.begin(m)
    s.record(m, m <= LIMIT, "estable" if m <= LIMIT else "error de verificación")
print("pasos:", [(x["mhz"], x["ok"]) for x in s.steps])
print("razón:", s.reason)
assert s.last_good == LIMIT and s.recommended() == LIMIT - 25 and s.recommended(False) == LIMIT

# Cuelgue: el estado queda con 'pending' y al volver a abrir se registra como fallo
s2 = stability.Search(volts=1.25, start_mhz=4000, max_mhz=4600)
s2.begin(4000)
s2.record(4000, True, "estable")
s2.begin(4025)                      # aquí "se cuelga" el equipo
r = stability.Search.load()
print("tras reinicio:", r.done, r.reason, "· recomendado", r.recommended())
assert r.done and r.last_good == 4000 and r.recommended() == 4000   # sin margen si queda por debajo del inicio

# Llega al máximo sin fallar
s3 = stability.Search(volts=1.2, start_mhz=5500, max_mhz=5550)
while (m := s3.next_mhz()) is not None:
    s3.begin(m)
    s3.record(m, True, "estable")
print("máximo:", s3.reason, s3.last_good)
assert s3.last_good == 5550

print("topes:", {g: cpudb.max_manual_voltage(g) for g in ("Zen+", "Zen 2", "Zen 3", "Zen 4", "Zen 5")})
print("inicio 5500:", stability.default_start(4200, 4267))
print("OK")
