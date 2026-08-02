.PHONY: setup sim tap hmi points dump watch test test-e2e test-docker test-browser smoke \
        scenario scenario-S01 scenario-S03 scenario-S05 scenario-S06 \
        scenario-S01-docker scenario-S03-docker \
        detect learn-baseline lint security clean \
        up down reset status panel logs

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

MODBUS_LOG ?= logs/modbus.log

# Use whichever compose CLI is actually installed — standalone
# `docker-compose` or the `docker compose` plugin — installs vary by
# platform/Docker version.
COMPOSE := $(shell command -v docker-compose >/dev/null 2>&1 && echo docker-compose || echo docker compose)

setup:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt
	$(VENV)/bin/pre-commit install

## --- running the plant ---

sim:
	$(PYTHON) -m process_sim.server

tap:
	$(PYTHON) -m sensor.tap --log $(MODBUS_LOG)

hmi:
	$(PYTHON) -m tools.hmi_poll --source-ip 127.0.0.1

points:
	$(PYTHON) -m tools.modctl points

dump:
	$(PYTHON) -m tools.modctl dump

watch:
	$(PYTHON) -m tools.modctl watch

## --- scenarios (require: make sim, make tap, make hmi running) ---

scenario:
	$(PYTHON) -m scenarios.menu

scenario-S01:
	$(PYTHON) -m attacker.s01_recon --port 5020 --source-ip 127.0.0.2

scenario-S03:
	$(PYTHON) -m attacker.s03_unauthorized_command --port 5020 --source-ip 127.0.0.2

scenario-S05:
	$(PYTHON) -m attacker.s05_manipulation_of_view --port 5020 --source-ip 127.0.0.2

## --- M4: same scenarios, run from a real attacker container on
## zone-enterprise, through the router, over the real Docker network
## topology (docker-compose.yml) instead of loopback + --source-ip
## spoofing. Requires `make up` running first. --no-deps matters: without
## it, compose re-triggers openplc-configure (a live PLC restart) on
## every run. See docs/architecture.md M4. Zeek/Suricata logs land in the
## zeek-logs volume; see `docker exec ot-range-router-1 cat
## /zeek-logs/modbus.log` or tests/test_router.py for how to pull them
## out. S05 has no -docker target: it spoofs a field-device register
## that sits below OpenPLC in this topology and is architecturally
## unreachable from zone-enterprise even through the router — see
## docs/architecture.md M4 for why that's a real finding, not a bug.

scenario-S01-docker:
	$(COMPOSE) --profile attacker run --rm --no-deps attacker \
		attacker.s01_recon --host router --port 502

scenario-S03-docker:
	$(COMPOSE) --profile attacker run --rm --no-deps attacker \
		attacker.s03_unauthorized_command --host router --port 502 \
		--map plc/modbus-map-openplc.yml

detect:
	$(PYTHON) -m sensor.detect $(MODBUS_LOG)

learn-baseline:
	$(PYTHON) -m sensor.detect $(MODBUS_LOG) --learn

## --- M1.5: process-sim + OpenPLC via docker-compose ---

up:
	# Built directly with `docker build`, not `docker-compose build`/`up
	# --build`: compose's build path requires buildx >= 0.17, which isn't
	# what ships in Debian's docker-buildx package (0.13.1). Tags match
	# what compose expects for each service (<project>-<service>) so it
	# picks them up without trying to build them itself.
	docker build -t ot-range-process-sim -f process_sim/Dockerfile .
	docker build -t ot-range-openplc -f plc/openplc/Dockerfile plc/openplc
	docker build -t ot-range-openplc-configure -f process_sim/Dockerfile .
	docker build -t ot-range-hmi -f hmi/Dockerfile .
	docker build -t ot-range-historian -f historian/Dockerfile .
	docker build -t ot-range-router -f router/Dockerfile .
	docker build -t ot-range-attacker -f attacker/Dockerfile .
	$(COMPOSE) up --wait
	@echo
	@echo "OpenPLC web UI: http://localhost:8080 (openplc / openplc)"
	@echo "HMI (operator display): http://localhost:8090"
	@echo "Grafana (historian): http://localhost:3000 (admin / admin)"
	@echo "SECURITY.md applies: simulated environment only."

down:
	$(COMPOSE) down

reset:
	# Wipes Postgres and the Zeek/Suricata log volume, then restarts
	# against the images already built by `make up` — no rebuild, so
	# this is fast enough to run between back-to-back scenarios.
	$(COMPOSE) down -v
	$(COMPOSE) up --wait
	@echo "State reset: fresh postgres, fresh zeek-logs volume."

status:
	$(PYTHON) tools/status.py

panel:
	$(PYTHON) -m panel.app

logs:
	$(COMPOSE) logs -f

# S06 targets OpenPLC's own web UI + Modbus interface directly (both
# published to loopback by `make up`), not the loopback Python sim —
# the attack surface is OpenPLC's admin interface itself, which the
# other scenarios don't touch. See scenarios/S06-logic-modification/.
scenario-S06:
	$(PYTHON) -m attacker.s06_logic_modification

## --- tests and gates ---

test:
	$(PYTHON) -m pytest -m "not e2e and not docker and not browser"

test-e2e:
	$(PYTHON) -m pytest -m e2e

test-docker:
	$(PYTHON) -m pytest -m docker -v

test-browser:
	$(PYTHON) -m pytest -m browser -v

smoke:
	PYTHON=$(PYTHON) bash tests/smoke.sh

lint:
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

security:
	$(VENV)/bin/pre-commit run --all-files
	$(VENV)/bin/bandit -c pyproject.toml -r .

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache **/__pycache__ logs
