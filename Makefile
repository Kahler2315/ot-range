.PHONY: setup sim tap hmi points dump watch test test-e2e smoke \
        scenario-S01 scenario-S03 detect learn-baseline lint security clean

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

MODBUS_LOG ?= logs/modbus.log

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

## --- tests and gates ---

test:
	$(PYTHON) -m pytest -m "not e2e"

test-e2e:
	$(PYTHON) -m pytest -m e2e

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
