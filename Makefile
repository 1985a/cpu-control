PREFIX  ?= /usr
DESTDIR ?=
LIBDIR  := $(DESTDIR)$(PREFIX)/lib/cpu-control

.PHONY: install uninstall run test lint

install:
	install -Dm644 -t $(LIBDIR)/cpucontrol cpucontrol/*.py
	install -Dm755 bin/cpu-control-helper $(LIBDIR)/cpu-control-helper
	install -Dm755 bin/cpu-control-daemon $(LIBDIR)/cpu-control-daemon
	install -Dm755 bin/cpu-control $(DESTDIR)$(PREFIX)/bin/cpu-control
	install -Dm644 data/org.cpucontrol.policy $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.cpucontrol.policy
	install -Dm644 data/cpu-control.service $(DESTDIR)$(PREFIX)/lib/systemd/system/cpu-control.service
	install -Dm755 data/cpu-control.sleep $(DESTDIR)$(PREFIX)/lib/systemd/system-sleep/cpu-control
	install -Dm644 data/org.cpucontrol.CpuControl.desktop $(DESTDIR)$(PREFIX)/share/applications/org.cpucontrol.CpuControl.desktop
	install -Dm644 data/org.cpucontrol.CpuControl.svg $(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps/org.cpucontrol.CpuControl.svg

uninstall:
	rm -rf $(LIBDIR)
	rm -f $(DESTDIR)$(PREFIX)/bin/cpu-control \
	      $(DESTDIR)$(PREFIX)/share/polkit-1/actions/org.cpucontrol.policy \
	      $(DESTDIR)$(PREFIX)/lib/systemd/system/cpu-control.service \
	      $(DESTDIR)$(PREFIX)/lib/systemd/system-sleep/cpu-control \
	      $(DESTDIR)$(PREFIX)/share/applications/org.cpucontrol.CpuControl.desktop \
	      $(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps/org.cpucontrol.CpuControl.svg

# Desde el repositorio, sin instalar (solo monitor)
run:
	./bin/cpu-control

# Pruebas de lógica con procesadores simulados (no tocan el hardware)
test:
	python3 tests/test_logic.py
	python3 tests/test_assist.py

# Análisis estático (configuración en pyproject.toml)
lint:
	uvx ruff check .
