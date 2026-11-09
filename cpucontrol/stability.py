"""Asistente de OC manual basado en la guía de michaelnager (r/ryzen):

  1. Tope de voltaje fijo (1.30 V en Zen 2/3, 1.20 V en Zen 4/5).
  2. Subir la frecuencia de todos los núcleos de 25 en 25 MHz.
  3. Probar cada paso con carga; el último paso estable es el resultado.

Diferencias con la guía original, para hacerlo más seguro en Linux:
  * En lugar de Cinebench se usa stress-ng con --verify (detecta errores de cálculo, no solo cuelgues).
  * Se corta la prueba si la temperatura llega al tope.
  * Antes de cada paso se guarda el estado en disco: si el equipo se cuelga y reinicias,
    el asistente sabe qué frecuencia falló y retoma desde la última estable.
  * Los ajustes de cada paso duran solo hasta reiniciar (nunca se aplican al arranque).
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field

from . import paths
from .profile import USER_DIR

STATE_FILE = os.path.join(USER_DIR, "assistant.json")
STEP_MHZ = 25


@dataclass
class Step:
    mhz: int
    ok: bool
    detail: str
    max_temp: float | None = None
    seconds: float | None = None


@dataclass
class Search:
    volts: float
    start_mhz: int
    max_mhz: int
    duration_s: int = 120
    temp_limit: int = 85
    step_mhz: int = STEP_MHZ
    steps: list = field(default_factory=list)
    pending: int | None = None          # frecuencia en prueba (si el equipo se cuelga queda aquí)
    done: bool = False
    reason: str = ""
    started: float = field(default_factory=time.time)

    # --- lógica ---
    @property
    def last_good(self) -> int | None:
        good = [s["mhz"] if isinstance(s, dict) else s.mhz for s in self.steps
                if (s["ok"] if isinstance(s, dict) else s.ok)]
        return max(good) if good else None

    def next_mhz(self) -> int | None:
        if self.done:
            return None
        lg = self.last_good
        nxt = self.start_mhz if lg is None else lg + self.step_mhz
        if nxt > self.max_mhz:
            self.finish(f"Se llegó al máximo permitido ({self.max_mhz} MHz) sin fallos.")
            return None
        return nxt

    def begin(self, mhz: int) -> None:
        self.pending = mhz
        self.save()

    def record(self, mhz: int, ok: bool, detail: str, max_temp=None, seconds=None) -> None:
        self.steps.append(asdict(Step(mhz, ok, detail, max_temp, seconds)))
        self.pending = None
        if not ok:
            lg = self.last_good
            self.finish(f"Falló a {mhz} MHz ({detail})." + (f" Último estable: {lg} MHz." if lg else
                        " Ni la frecuencia inicial fue estable: baja la frecuencia inicial."))
        self.save()

    def finish(self, reason: str) -> None:
        self.done = True
        self.reason = reason
        self.save()

    def recommended(self, margin: bool = True) -> int | None:
        lg = self.last_good
        if lg is None:
            return None
        return lg - self.step_mhz if margin and lg - self.step_mhz >= self.start_mhz else lg

    # --- persistencia ---
    def save(self) -> None:
        os.makedirs(USER_DIR, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(asdict(self), f, indent=2)
            f.flush()
            os.fsync(f.fileno())   # que sobreviva a un cuelgue
        os.replace(tmp, STATE_FILE)

    @classmethod
    def load(cls) -> Search | None:
        try:
            with open(STATE_FILE) as f:
                d = json.load(f)
            s = cls(**{k: d[k] for k in d if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return None
        if s.pending is not None and not s.done:
            # Había una prueba en curso cuando se cerró la sesión: casi seguro un cuelgue o reinicio
            s.record(s.pending, False, "el equipo se colgó o se reinició durante la prueba")
        return s

    @staticmethod
    def clear() -> None:
        with contextlib.suppress(FileNotFoundError):
            os.remove(STATE_FILE)


def stress_tool() -> str | None:
    return shutil.which("stress-ng")


def stress_command(seconds: int) -> list[str] | None:
    # Atajo de las pruebas (tests/shoot.py): solo con el equipo simulado (CPU_CONTROL_SYSROOT)
    fake = os.environ.get("CPU_CONTROL_FAKE_STRESS")
    if fake and paths.SYSROOT:
        return ["sh", "-c", fake]
    tool = stress_tool()
    if not tool:
        return None
    # Todos los hilos, todos los métodos de CPU, verificando cada resultado
    return [tool, "--cpu", "0", "--cpu-method", "all", "--verify", "--timeout", f"{seconds}s",
            "--metrics-brief"]


def default_start(boost_mhz: int | None, hw_max_mhz: int | None) -> int:
    """Frecuencia inicial: 200 MHz por debajo del boost de fábrica (siempre estable con ese voltaje)."""
    top = boost_mhz or hw_max_mhz or 4000
    return int(max(2500, (top - 200)) // STEP_MHZ * STEP_MHZ)


BIOS_STEPS_GENERIC = [
    ("Actualiza la BIOS", "Usa la última versión (AGESA) de tu placa."),
    ("Carga los valores por defecto", "Parte de una configuración limpia."),
    ("LLC del Vcore", "Load-Line Calibration del núcleo en el 2.º o 3.º nivel más fuerte, nunca el máximo."),
    ("LLC del SoC", "Igual para el SoC: 2.º o 3.º nivel más fuerte."),
    ("Perfil XMP / DOCP", "Activa el perfil de tu RAM."),
    ("PBO desactivado", "«Disabled» de verdad, no «Auto»."),
]
BIOS_STEPS_MSI = [
    ("Actualiza la BIOS", "Usa la última versión de tu placa en msi.com (en B350, la que da soporte a Ryzen 5000)."),
    ("Carga los valores por defecto", "F6 en Click BIOS 5, guarda y reinicia."),
    ("CPU LoadLine Calibration Control",
     "OC › DigitALL Power: en MSI normalmente el Mode 1 es el más fuerte; usa Mode 2 o 3."),
    ("CPU NB/SoC LoadLine Calibration Control", "En el mismo menú: Mode 2 o 3."),
    ("A-XMP", "Actívalo en la pantalla principal o en OC › DRAM Setting."),
    ("Precision Boost Overdrive = Disabled", "OC › Advanced CPU Configuration (o AMD Overclocking): «Disabled», "
                                             "no «Auto»."),
]
