.PHONY: help install test conformance fuzz-smoke fuzz report clean

PY := .venv/bin/python
CORPUS := third_party/toml-test/tests/valid

help:
	@echo "make install      create venv and install deps"
	@echo "make test         run pytest unit tests"
	@echo "make conformance  run official toml-test v1.0.0 corpus"
	@echo "make fuzz-smoke   short 2k fuzz campaign"
	@echo "make fuzz         longer 100k campaign"
	@echo "make report DIR=out/fuzz   render Markdown report"

install:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e '.[fuzz]' pytest

test:
	$(PY) -I -m pytest tests/ -q

conformance:
	$(PY) -I tools/conformance/run_conformance.py

fuzz-smoke:
	$(PY) -I tools/fuzz/run_fuzz.py -n 2000 --seed 7 --out out/fuzz-smoke

fuzz:
	$(PY) -I tools/fuzz/run_fuzz.py -n 100000 --seed 20261117 \
		--out out/fuzz --seed-corpus $(CORPUS)

report:
	$(PY) -I tools/fuzz/report.py $(DIR) -o $(DIR)/REPORT.md

clean:
	rm -rf out build *.egg-info src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
