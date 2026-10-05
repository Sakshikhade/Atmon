.PHONY: help validate-local validate-docs validate-capability-matrix test-detect test-family build-apps

help:
	@echo "ATMON (atmos-proj) — developer verbs"
	@echo ""
	@echo "  make validate-local              Run quality gates (detect, family, builds, docs, capability matrix)"
	@echo "  make validate-docs               Audit markdown relative links"
	@echo "  make validate-capability-matrix  Audit F-ID inventory in capability CSV"
	@echo "  make test-detect                 Pytest in action_detection"
	@echo "  make test-family                 Family detector mapper smoke test"
	@echo "  make build-apps                  Production build family + clinician"
	@echo ""
	@echo "Local stack (manual terminals):"
	@echo "  cd action_detection && PORT=8010 python webapp/server.py"
	@echo "  cd apps/family && npm run dev          # :5173"
	@echo "  cd apps/clinician && npm run dev       # :5180"
	@echo ""
	@echo "Docs: docs/README.md   Agents: AGENTS.md   Status: STATUS.md"

test-detect:
	cd action_detection && (test -x .venv/bin/python && .venv/bin/python -m pytest -q || python3 -m pytest -q)

test-family:
	cd apps/family && npx --yes tsx --test src/lib/detector.test.ts

build-apps:
	cd apps/family && npm run build
	cd apps/clinician && npm run build

validate-docs:
	python3 scripts/audit_docs.py

validate-capability-matrix:
	python3 scripts/audit_capability_matrix.py

validate-local: test-detect test-family build-apps validate-docs validate-capability-matrix
	@echo ""
	@echo "validate-local: all gates passed"
