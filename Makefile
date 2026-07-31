.PHONY: setup sim tap hmi points dump watch test test-e2e test-docker smoke \
        scenario-S01 scenario-S03 detect learn-baseline lint security clean \
        up down logs

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

scenario-S01:
	$(PYTHON) -m attacker.s01_recon --port 5020 --source-ip 127.0.0.2

scenario-S03:
	$(PYTHON) -m attacker.s03_unauthorized_command --port 5020 --source-ip 127.0.0.2

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
	$(COMPOSE) up --wait
	@echo
	@echo "OpenPLC web UI: http://localhost:8080 (openplc / openplc)"
	@echo "HMI (operator display): http://localhost:8090"
	@echo "SECURITY.md applies: simulated environment only."

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

## --- tests and gates ---

test:
	$(PYTHON) -m pytest -m "not e2e and not docker"

test-e2e:
	$(PYTHON) -m pytest -m e2e

test-docker:
	$(PYTHON) -m pytest -m docker -v

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
