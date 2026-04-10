ROOT_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
VENV ?= .venv
PYTHON ?= $(ROOT_DIR)$(VENV)/bin/python
PIP ?= $(ROOT_DIR)$(VENV)/bin/pip
UVICORN ?= $(ROOT_DIR)$(VENV)/bin/uvicorn
ALEMBIC ?= $(ROOT_DIR)$(VENV)/bin/alembic

POSTGRES_TEST_URL ?= postgresql+asyncpg://copilot:copilot@localhost:55432/copilot_db
PHASE2_ENV = DATABASE_URL=$(POSTGRES_TEST_URL) QDRANT_LOCATION=:memory: EMBEDDING_DIMENSION=16 EMBEDDING_BACKEND=fallback PARSER_BACKEND=fallback PYTHONPYCACHEPREFIX=/tmp/pycache

.PHONY: dev-up dev-restart backend-install backend-install-full backend-migrate backend-run gateway-run phase2-test phase2-test-api phase3-test phase4-test phaseb-test phasec-test phased-test phasee-test phasev2-test-api pdf-audit sample-manifest case-library historical-corpus

dev-up:
	./scripts/dev-up.sh

dev-restart:
	./scripts/dev-up.sh restart

backend-install:
	python3 -m venv $(VENV)
	$(PIP) install -e ./backend

backend-install-full:
	python3 -m venv $(VENV)
	$(PIP) install -e './backend[full]'

backend-migrate:
	cd backend && $(ALEMBIC) upgrade head

backend-run:
	cd backend && $(UVICORN) app.main:app --reload

gateway-run:
	cd gateway && REDIS_ENABLED=false $(UVICORN) app.main:app --reload --port 8001

phase2-test:
	cd backend && env PYTHONPYCACHEPREFIX=/tmp/pycache $(PYTHON) -m unittest tests.test_parsing tests.test_document_profile tests.test_sample_manifest tests.test_case_library tests.test_case_retrieval tests.test_table_profile tests.test_formula_candidates tests.test_formula_regions tests.test_formula_ocr tests.test_pdf_audit tests.test_ingestion_filter tests.test_retrieval tests.test_storage

phase2-test-api:
	cd backend && env $(PHASE2_ENV) $(PYTHON) -m unittest tests.test_api_phase2

phase3-test:
	cd gateway && env REDIS_ENABLED=false PYTHONPYCACHEPREFIX=/tmp/pycache ../$(VENV)/bin/python -m unittest tests.test_detector tests.test_masker tests.test_restorer tests.test_gateway
	cd backend && env PYTHONPYCACHEPREFIX=/tmp/pycache $(PYTHON) -m unittest tests.test_gateway_client

phase4-test:
	cd backend && env PYTHONPYCACHEPREFIX=/tmp/pycache $(PYTHON) -m unittest tests.test_llm_client
	cd backend && env GATEWAY_MASKING_ENABLED=false PYTHONPYCACHEPREFIX=/tmp/pycache $(PYTHON) -m unittest tests.test_generation_api tests.test_review_api

phaseb-test:
	cd backend && env PYTHONPYCACHEPREFIX=/tmp/pycache $(PYTHON) -m unittest tests.test_v2_schema tests.test_requirement_pipeline tests.test_artifacts_api

phasec-test:
	cd backend && env PYTHONPYCACHEPREFIX=/tmp/pycache $(PYTHON) -m unittest tests.test_composition_helpers tests.test_composition_api

phased-test:
	cd backend && env PYTHONPYCACHEPREFIX=/tmp/pycache $(PYTHON) -m unittest tests.test_validation_helpers tests.test_validation_api

phasee-test:
	cd backend && env PYTHONPYCACHEPREFIX=/tmp/pycache $(PYTHON) -m unittest tests.test_export_helpers tests.test_export_api

phasev2-test-api:
	cd backend && env $(PHASE2_ENV) GATEWAY_MASKING_ENABLED=false LLM_PROVIDER_BACKEND=mock $(PYTHON) -m unittest tests.test_api_v2_pipeline

pdf-audit:
	@if [ -z "$(PDF)" ]; then echo "Usage: make pdf-audit PDF=/absolute/path/to/file.pdf"; exit 1; fi
	cd backend && env PARSER_BACKEND=docling PYTHONPYCACHEPREFIX=/tmp/pycache ../$(VENV)/bin/python scripts/pdf_parse_audit.py "$(PDF)" $(if $(FORMULA_OCR_BACKEND),--formula-ocr-backend $(FORMULA_OCR_BACKEND),) $(if $(FORMULA_OCR_MAX_ASSETS),--formula-ocr-max-assets $(FORMULA_OCR_MAX_ASSETS),) $(if $(FORMULA_OCR_MAX_REGIONS_PER_ASSET),--formula-ocr-max-regions-per-asset $(FORMULA_OCR_MAX_REGIONS_PER_ASSET),)

sample-manifest:
	@if [ -z "$(PATHS)" ]; then echo "Usage: make sample-manifest PATHS=/absolute/path/or/dir [WITH_PROFILE=1]"; exit 1; fi
	cd backend && env PARSER_BACKEND=docling PYTHONPYCACHEPREFIX=/tmp/pycache ../$(VENV)/bin/python scripts/build_sample_manifest.py $(PATHS) $(if $(WITH_PROFILE),--with-profile,) $(if $(OUTPUT_JSON),--output-json $(OUTPUT_JSON),) $(if $(OUTPUT_MD),--output-md $(OUTPUT_MD),) $(if $(ASSIGNED_TRACK),--assigned-track $(ASSIGNED_TRACK),)

case-library:
	@if [ -z "$(MANIFEST)" ]; then echo "Usage: make case-library MANIFEST=backend/data/sample_manifests/sample_manifest.json [INCLUDE_HOLDOUT=1]"; exit 1; fi
	cd backend && env PARSER_BACKEND=docling PYTHONPYCACHEPREFIX=/tmp/pycache ../$(VENV)/bin/python scripts/build_case_library.py --manifest "$(MANIFEST)" $(if $(OUTPUT_DIR),--output-dir $(OUTPUT_DIR),) $(if $(INCLUDE_HOLDOUT),--include-holdout,)

historical-corpus:
	@if [ -z "$(MANIFEST)" ]; then echo "Usage: make historical-corpus MANIFEST=data/sample_manifests/sample_manifest.json [TRACKS=pilot_main] [RECREATE_COLLECTION=1]"; exit 1; fi
	cd backend && env PARSER_BACKEND=docling PYTHONPYCACHEPREFIX=/tmp/pycache ../$(VENV)/bin/python scripts/import_historical_corpus.py --manifest "$(MANIFEST)" $(if $(TRACKS),--tracks "$(TRACKS)",) $(if $(DOC_TYPE),--doc-type "$(DOC_TYPE)",) $(if $(RECREATE_COLLECTION),--recreate-collection,)
