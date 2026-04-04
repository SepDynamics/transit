.PHONY: install install-cluster frontend-install frontend-typecheck frontend-build build-manifold-engine test test-all test-cluster test-legacy-cluster test-transit check check-all check-cluster check-legacy-cluster check-transit check-transit-case-packs clean cluster-collector cluster-regime cluster-policy cluster-api cluster-replay cluster-trace-record cluster-trace-import cluster-compare cluster-generate-eval-fixture transit-archive transit-mbta-archive transit-ingest transit-replay transit-api transit-history-report transit-calibration-report transit-calibration-summary

PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
PIP_FLAGS ?= --no-cache-dir
PIP_BREAK_FLAG ?= --break-system-packages
FRONTEND_NODE_MODULES_STAMP := apps/frontend/node_modules/.installed.stamp

install:
	$(PIP) install $(PIP_FLAGS) -r requirements.txt || \
		$(PIP) install $(PIP_FLAGS) $(PIP_BREAK_FLAG) -r requirements.txt

install-cluster:
	$(PIP) install $(PIP_FLAGS) -r requirements.cluster.txt || \
		$(PIP) install $(PIP_FLAGS) $(PIP_BREAK_FLAG) -r requirements.cluster.txt

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

test-cluster: test-legacy-cluster

test-legacy-cluster:
	$(PYTHON) -m pytest tests/cluster

test-transit:
	$(PYTHON) -m pytest tests/transit

check: check-transit

check-all: test-all frontend-typecheck frontend-build

check-cluster: check-legacy-cluster

check-legacy-cluster: test-legacy-cluster

check-transit: test-transit check-transit-case-packs frontend-typecheck frontend-build

check-transit-case-packs:
	@PYTHONPATH=. $(PYTHON) scripts/transit/grade_calibration.py --archive-root data/case-packs --labels data/case-packs --strict

cluster-collector:
	@PYTHONPATH=. $(PYTHON) scripts/cluster/telemetry_collector.py $(ARGS)

cluster-regime:
	@PYTHONPATH=. $(PYTHON) scripts/cluster/regime_service.py $(ARGS)

cluster-policy:
	@PYTHONPATH=. $(PYTHON) scripts/cluster/policy_engine.py $(ARGS)

cluster-api:
	@PYTHONPATH=. $(PYTHON) scripts/cluster/api.py $(ARGS)

cluster-replay:
	@PYTHONPATH=. $(PYTHON) scripts/cluster/replay.py $(ARGS)

cluster-trace-record:
	@PYTHONPATH=. $(PYTHON) scripts/cluster/trace_recorder.py $(ARGS)

cluster-trace-import:
	@PYTHONPATH=. $(PYTHON) scripts/cluster/trace_import.py $(ARGS)

cluster-compare:
	@PYTHONPATH=. $(PYTHON) scripts/cluster/compare_baseline.py $(ARGS)

cluster-generate-eval-fixture:
	@PYTHONPATH=. $(PYTHON) scripts/cluster/generate_eval_fixture.py $(ARGS)

transit-archive:
	@PYTHONPATH=. $(PYTHON) scripts/transit/archive.py $(ARGS)

transit-mbta-archive:
	@PYTHONPATH=. $(PYTHON) scripts/transit/archive.py $(ARGS)

transit-ingest:
	@PYTHONPATH=. $(PYTHON) scripts/transit/ingest.py $(ARGS)

transit-replay:
	@PYTHONPATH=. $(PYTHON) scripts/transit/replay.py $(ARGS)

transit-api:
	@PYTHONPATH=. $(PYTHON) scripts/transit/api.py $(ARGS)

transit-history-report:
	@PYTHONPATH=. $(PYTHON) scripts/transit/report.py $(ARGS)

transit-calibration-report:
	@PYTHONPATH=. $(PYTHON) scripts/transit/grade_calibration.py $(ARGS)

transit-calibration-summary:
	@PYTHONPATH=. $(PYTHON) scripts/transit/render_calibration_summary.py $(ARGS)

clean:
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
	rm -rf apps/frontend/node_modules apps/frontend/dist .pytest_cache
	rm -f manifold_engine*.so
