.PHONY: install frontend-install frontend-typecheck frontend-build build-manifold-engine test test-all test-transit check check-all check-transit check-transit-case-packs clean transit-archive transit-mbta-archive transit-ingest transit-replay transit-api transit-api-parity transit-live-health transit-prune-history transit-history-report transit-calibration-report transit-calibration-summary transit-benchmark-artifacts transit-demo-seed transit-notify transit-proof-window

PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
PIP_FLAGS ?= --no-cache-dir
PIP_BREAK_FLAG ?= --break-system-packages
FRONTEND_NODE_MODULES_STAMP := apps/frontend/node_modules/.installed.stamp

install:
	$(PIP) install $(PIP_FLAGS) -r requirements.txt || \
		$(PIP) install $(PIP_FLAGS) $(PIP_BREAK_FLAG) -r requirements.txt

$(FRONTEND_NODE_MODULES_STAMP): apps/frontend/package.json apps/frontend/package-lock.json
	cd apps/frontend && npm ci
	@touch $@

frontend-install: $(FRONTEND_NODE_MODULES_STAMP)

frontend-typecheck: $(FRONTEND_NODE_MODULES_STAMP)
	cd apps/frontend && npm run typecheck

frontend-build: $(FRONTEND_NODE_MODULES_STAMP)
	cd apps/frontend && npm run build

build-manifold-engine:
	@sh scripts/build_manifold_engine.sh

test: test-transit

test-all:
	$(PYTHON) -m pytest tests

test-transit:
	$(PYTHON) -m pytest tests/transit

check: check-transit

check-all: test-all frontend-typecheck frontend-build

check-transit: test-transit check-transit-case-packs frontend-typecheck frontend-build

check-transit-case-packs:
	@PYTHONPATH=. $(PYTHON) scripts/transit/grade_calibration.py --archive-root data/case-packs/mbta --labels data/case-packs/mbta --strict

transit-archive:
	@PYTHONPATH=. $(PYTHON) scripts/transit/archive.py $(ARGS)

transit-mbta-archive:
	@PYTHONPATH=. $(PYTHON) scripts/transit/archive.py --agency mbta $(ARGS)

transit-ingest:
	@PYTHONPATH=. $(PYTHON) scripts/transit/ingest.py $(ARGS)

transit-replay:
	@PYTHONPATH=. $(PYTHON) scripts/transit/replay.py $(ARGS)

transit-api:
	@PYTHONPATH=. $(PYTHON) scripts/transit/api.py $(ARGS)

transit-api-parity:
	@PYTHONPATH=. $(PYTHON) scripts/transit/api_parity.py $(ARGS)

transit-live-health:
	@PYTHONPATH=. $(PYTHON) scripts/transit/live_health.py $(ARGS)

transit-prune-history:
	@PYTHONPATH=. $(PYTHON) scripts/transit/prune_history.py $(ARGS)

transit-history-report:
	@PYTHONPATH=. $(PYTHON) scripts/transit/report.py $(ARGS)

transit-calibration-report:
	@PYTHONPATH=. $(PYTHON) scripts/transit/grade_calibration.py $(ARGS)

transit-calibration-summary:
	@PYTHONPATH=. $(PYTHON) scripts/transit/render_calibration_summary.py $(ARGS)

transit-benchmark-artifacts:
	@PYTHONPATH=. $(PYTHON) scripts/transit/benchmark_artifacts.py $(ARGS)

transit-demo-seed:
	@PYTHONPATH=. $(PYTHON) scripts/transit/demo_seed.py $(ARGS)

transit-notify:
	@PYTHONPATH=. $(PYTHON) scripts/transit/notify.py $(ARGS)

transit-proof-window:
	@PYTHONPATH=. $(PYTHON) scripts/transit/proof_windows.py $(ARGS)

clean:
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	rm -rf apps/frontend/node_modules apps/frontend/dist .pytest_cache
	rm -f manifold_engine*.so
