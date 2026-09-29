# Autism Activity Monitoring & Alerting System (AAMAS)

AAMAS is a lightweight, self-contained local Python edge monitor and video ingest tool designed for privacy-first behavioral state detection in real time — **Normal**, **Stimming** (e.g. repetitive hand-flapping, including a dedicated ears-closed subtype), and **Physical Avoidance** (covering ears, closing eyes) — with real-time alerting via Webhooks and optional Twilio SMS.

It runs a decoupled, multi-threaded computer vision pipeline at ~30 FPS on consumer hardware, processing frames entirely in memory with no raw video ever written to disk or sent to the cloud.

---

## Executive Summary

- **Core Mission & Architecture**: Fully on-device, self-contained Python edge system providing real-time behavioral monitoring and offline video analysis without external cloud or web framework dependencies.
- **Privacy-First Telemetry**: In-memory frame processing with zero video or facial pixel persistence. Stores only downsampled 11-joint 3D coordinate trajectories (`data/outbox.db`) for playback visualization.
- **Full-Stack Edge Experience**: Includes an offline headless video ingestion CLI (`src.ingest`), a zero-dependency local web server (`src.server`), and a single-file HTML5/Canvas interactive 3D skeleton replay dashboard.
- **Immediate Multi-Channel Alerting**: Dispatches sub-second behavioral episode alerts via asynchronous HTTP Webhooks and optional Twilio SMS.
- **Verification & Reliability**: 100% automated test coverage across 11 modules (48 unit/integration tests) and strict style/linter compliance.

---

## Key Features

- **Real-Time Edge Monitoring**: Non-blocking camera stream with neon visual HUD overlay and real-time state classification.
- **Offline Recorded Video Ingestion**: Batch process video files headlessly, logging frame features to local CSV (`data/behavior_log.csv`), buffering skeleton snapshots, and saving episodes to SQLite (`data/outbox.db`).
- **Standalone Single-File Dashboard**: Lightweight local Python web server (`src/server.py`) serving an interactive HTML5 canvas skeleton replay player with date/time filtering and live statistics.
- **Dual Alerting Pathway**: Real-time dispatch via asynchronous HTTP Webhooks and optional SMS notifications via Twilio.
- **Strict Privacy**: In-memory frame processing with immediate memory purging on every loop iteration — only anonymized numeric skeleton coordinates are persisted.
- **Dual Classification Modes**: Rule-based heuristic analysis (FFT frequency + velocity) and trained Machine Learning classifier (Random Forest).

---

## Quick Start

```bash
# 1. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure local environment (optional)
cp .env.example .env

# 4. Start local webhook receiver (optional, terminal 1)
python mock_server.py

# 5. Run live camera monitor (terminal 2)
python src/main.py

# 6. Or analyze a pre-recorded video file into local SQLite:
python -m src.ingest samples/sample-2.mp4

# 7. Start the standalone web dashboard & skeleton replay viewer:
python -m src.server --port 8000
# Open http://localhost:8000 in your browser
```

Run automated tests with `pytest`. No webcam required.

---

## Project Structure

| Path | Contents |
| :--- | :--- |
| `apps/` | Family and clinician Vite apps (Supabase-backed review UX) |
| `action_detection/` | Git submodule — few-shot X-CLIP action detector ([Atmon](https://github.com/Sakshikhade/Atmon)); post-capture API on `:8010` |
| `src/` | Python edge monitor, CV pipeline, ML/heuristic classifiers, ingest CLI, event storage, dashboard HTTP server, and alert gateway |
| `static/` | Standalone single-file HTML/JS/CSS dashboard and skeleton replay visualizer |
| `tests/` | Unit test suite covering detectors, streams, ingestion, event storage, server, and alert dispatchers |
| `samples/` | Sample videos for testing and local playback |
| `scripts/` | Development helper scripts (`scripts/reset-local.sh` for resetting local database) |
| `data/` | Runtime artifacts (`data/outbox.db` SQLite database and CSV behavior logs) — gitignored |
| `mock_server.py` | Local webhook receiver for development and testing |
| `train_classifier.py` | Machine learning model training script |
| `doc/` | Technical documentation and developer guides |

---

## Testing & Linting

```bash
# Run unit tests
pytest

# Run linter
ruff check .
```

---

## Documentation

- [Development Guide](doc/development.md) — Comprehensive setup, running modes, alerting configuration, and technical architecture.
- [Functional FAQ](doc/faq.md) — Behavioral detection mechanics, algorithms, and thresholds.
- [Architecture](doc/architecture.md) — Pipeline architecture, thread model, and latency profiling.
- [Demo Guide](doc/demo_guide.md) — Step-by-step walkthrough of live monitoring, video ingest, and alerting.
- [Product Scope](doc/product-scope.md) — System scope, goals, target users, and privacy principles.
- [Test Plan](doc/test_plan.md) — Comprehensive test strategy, test matrix, and verification protocols.
- [Post-capture detection](doc/detection.md) — Wiring the local action_detection (X-CLIP) service into the family app.

---

## License

See [LICENSE](LICENSE).
