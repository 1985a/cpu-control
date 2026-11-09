"""Helper privilegiado: la interfaz lo ejecuta con `pkexec` (polkit pide la contraseña).

  cpu-control-helper apply  < {"profile": {...}, "boot": true|false}
  cpu-control-helper reset
  cpu-control-helper experimental on|off
  cpu-control-helper start

Valida contra los límites que el servicio midió en el hardware, guarda el perfil y pide
al servicio que lo aplique; espera el resultado y lo devuelve en JSON.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from typing import NoReturn

from .paths import BOOT_FILE, ETC_DIR, SESSION_FILE, SETTINGS_FILE, STATUS_FILE
from .profile import Profile, ValidationError, validate

SERVICE = "cpu-control.service"
MAX_INPUT = 64 * 1024


def _out(obj, code=0) -> NoReturn:
    print(json.dumps(obj, ensure_ascii=False))
    sys.exit(code)


def _die(msg) -> NoReturn:
    _out({"ok": False, "error": msg}, 1)


def _systemctl(*args) -> int:
    return subprocess.run(["/usr/bin/systemctl", *args], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, check=False).returncode


def _status() -> dict:
    try:
        with open(STATUS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _ensure_running() -> dict:
    st = _status()
    if st and time.time() - st.get("time", 0) < 600 and _systemctl("is-active", "--quiet", SERVICE) == 0:
        return st
    _systemctl("start", SERVICE)
    for _ in range(50):
        time.sleep(0.2)
        st = _status()
        if st and _systemctl("is-active", "--quiet", SERVICE) == 0:
            return st
    _die("El servicio cpu-control no arrancó. Revisa: journalctl -u cpu-control")


def _write(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _remove(path: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)


def _reload_and_wait(t0: float) -> dict:
    _systemctl("reload-or-restart", SERVICE)
    for _ in range(100):
        time.sleep(0.15)
        st = _status()
        if st.get("active", {}).get("time", 0) >= t0:
            return st
    _die("El servicio no respondió a tiempo. Revisa: journalctl -u cpu-control")


def cmd_apply() -> None:
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        _die("Entrada demasiado grande")
    try:
        req = json.loads(raw)
        prof = Profile.from_dict(req["profile"])
        boot = req.get("boot", False)
        if not isinstance(boot, bool):
            raise TypeError
    except (ValueError, KeyError, TypeError, AttributeError):
        _die("JSON inválido")
    st = _ensure_running()
    try:
        validate(prof, st.get("limits", {}))
    except ValidationError as e:
        _die(str(e))
    t0 = time.time()
    data = {"profile": prof.to_dict(), "saved": t0}
    if boot:
        _write(BOOT_FILE, data)
        _remove(SESSION_FILE)
    else:
        _write(SESSION_FILE, data)
        _remove(BOOT_FILE)
    st = _reload_and_wait(t0)
    _out({"ok": True, "results": st.get("active", {}).get("results", [])})


def cmd_reset() -> None:
    _ensure_running()
    t0 = time.time()
    _remove(SESSION_FILE)
    _remove(BOOT_FILE)
    st = _reload_and_wait(t0)
    _out({"ok": True, "results": st.get("active", {}).get("results", [])})


def cmd_experimental(on: bool) -> None:
    try:
        with open(SETTINGS_FILE) as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    s["experimental"] = on
    os.makedirs(ETC_DIR, mode=0o755, exist_ok=True)
    _write(SETTINGS_FILE, s)
    # Reiniciar para recalcular las funciones disponibles
    _systemctl("restart", SERVICE)
    _out({"ok": True})


def cmd_start() -> None:
    _systemctl("enable", SERVICE)
    _ensure_running()
    _out({"ok": True})


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if os.geteuid() != 0:
        _die("Debe ejecutarse como root (vía pkexec)")
    if not argv:
        _die("Falta el comando")
    cmd = argv[0]
    if cmd == "apply":
        cmd_apply()
    elif cmd == "reset":
        cmd_reset()
    elif cmd == "experimental" and len(argv) == 2 and argv[1] in ("on", "off"):
        cmd_experimental(argv[1] == "on")
    elif cmd == "start":
        cmd_start()
    else:
        _die("Comando desconocido")


if __name__ == "__main__":
    main()
