.PHONY: help validate-local validate-docs test-detect test-family build-apps

help:
	@echo "ATMON (atmos-proj) — developer verbs"
	@echo ""
	@echo "  make validate-local   Run all 4 quality gates (detect, family, builds, docs)"
	@echo "  make validate-docs    Audit markdown relative links"
	@echo "  make test-detect      Pytest in action_detection"
	@echo "  make test-family      Family detector mapper smoke test"
	@echo "  make build-apps       Production build family + clinician"
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

validate-local: test-detect test-family build-apps validate-docs
	@echo ""
	@echo "validate-local: all gates passed"
