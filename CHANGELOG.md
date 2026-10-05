# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Nexus-style docs governance: `docs/` hub, root `AGENTS.md`, `STATUS.md`, `DESIGN.md`, `Makefile` quality gates, `.agents/skills/` (ops / sprint / PR).
- Sprint system seeded from backlog assessment (`docs/sprints/SPRINT_TRACKER.md`, Sprint 1–6 PROMPTs, `BACKLOG.md`).
- ATMON architecture overview + ADR-001 (post-capture family detect).
- `scripts/audit_docs.py` and `make validate-docs`.
- `docs/product/ATMON_Capability_Matrix.csv` F-ID status spreadsheet + `make validate-capability-matrix` inventory gate (detailed PASS/FAIL ACs; `QA` / `QA_Status` columns; default QA=Abhishek).
- Four-person sprint replan (Sakshi/Mateo/Abhishek/Mainak), W1–W3 dates through 2026-10-24, `TODOS.md` follow-ups.

### Changed
- Renamed `doc/` → `docs/`; archived legacy AAMAS docs under `docs/archive/legacy-aamas/`.
- Family detection remains post-capture only; session delete for recorder (prior product work on `new_implementations`).
- `CAPABILITY_MATRIX.md` is the product loop / integration map; CSV is F-ID status source of truth.
- Sprint tracker: S1-T06, S5-T06–T08, S6-T05a/T05; Hosting_Owner default Abhishek; pilot-blocker commit scope.

## [1.1.0] - 2026-09-02

### Added
- Local SQLite behavior event and skeleton snapshot persistence module (`src/event_storage.py`) in `data/outbox.db`.
- Standalone single-file HTML/JS/CSS dashboard (`static/index.html`) featuring real-time statistics, date/time filtering, episode timeline, and an interactive HTML5 canvas skeleton player.
- Built-in Python HTTP server and REST API (`src/server.py`) serving `/api/stats`, `/api/episodes`, and `/api/episodes/<id>`.
- Enhanced video ingestion CLI (`src/ingest.py`) and live camera monitor (`src/main.py`) to buffer 11-joint skeleton keypoint sequences and record episodes into SQLite.
- Local database reset utility script (`scripts/reset-local.sh`) and automated tests (`tests/test_reset_script.py`) to clean development SQLite databases and CSV logs.
- Unit and integration tests covering SQLite storage CRUD, query filters, snapshot generation, reset script, and REST API endpoints (`tests/test_event_storage.py`, `tests/test_server.py`, `tests/test_reset_script.py`).
- Executive Summary at the top of `README.md` detailing architecture, privacy-first telemetry, and edge capabilities.

### Changed
- Consolidated local environment configuration into `.env` and `.env.example`, removing redundant `.env.local`.

## [1.0.0] - 2026-09-01

### Added
- Standalone offline recorded video ingestion CLI (`src/ingest.py`) supporting single files and batch directory processing.
- Direct multi-channel alerting gateway (`src/alert_gateway.py`) supporting HTTP Webhook and Twilio SMS routing.
- Local CSV feature logging (`src/data_logger.py`) for offline dataset collection and model training.
- 6-feature scikit-learn Random Forest model training pipeline (`train_classifier.py`).

### Changed
- Transformed AAMAS into a lightweight, self-contained local Python edge monitor and video ingestion tool.
- Decoupled video capture into persistent background thread (`src/camera_stream.py`).
- Enhanced real-time HUD with neon state indicators, metric bars, and keyboard simulation controls.
- Consolidated documentation across `README.md`, `development.md`, `product-scope.md`, `architecture.md`, `test_plan.md`, `demo_guide.md`, and `faq.md`.

### Removed
- Removed Next.js web frontend (`web/`) and Vercel configuration.
- Removed Supabase backend migrations, Edge functions, and cloud sync client (`src/cloud_sync.py`, `src/event_outbox.py`).
- Removed obsolete cloud/RLS integration tests.
