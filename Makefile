.PHONY: setup sim points dump watch test smoke lint security clean

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

setup:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt
	$(VENV)/bin/pre-commit install

sim:
	$(PYTHON) -m process_sim.server

points:
	$(PYTHON) -m tools.modctl points

dump:
	$(PYTHON) -m tools.modctl dump

watch:
	$(PYTHON) -m tools.modctl watch

test:
	$(PYTHON) -m pytest

smoke:
	PYTHON=$(PYTHON) bash tests/smoke.sh

lint:
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

security:
	$(VENV)/bin/pre-commit run --all-files
	$(VENV)/bin/bandit -c pyproject.toml -r .

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache **/__pycache__
