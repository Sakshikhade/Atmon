# Development Guide

This guide covers everything needed to set up a local development environment, run the standalone AAMAS edge monitor and video ingestion tool, configure alerting, and contribute changes.

---

## 1. Prerequisites

- **Python 3.10+** (developed and tested on Python 3.11).
- A **webcam** (built-in or USB) for live monitoring. Not required for unit tests or video file ingestion.
- macOS or Linux. On Linux a display server (X11/Wayland) is needed for the HUD window; use `--headless` otherwise.

---

## 2. Environment Setup

All commands are run from the repository root:

```bash
# 1. Create an isolated virtual environment
python3 -m venv .venv

# 2. Activate it
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows PowerShell

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

`requirements.txt` includes runtime and test tooling (`pytest`, `pytest-mock`, `ruff`).

### Local Configuration (`.env`)

Copy the example environment template to `.env`:

```bash
cp .env.example .env
```

Available configuration keys in `.env`:

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `CAMERA_INDEX` | `0` | Camera device index (or path to video file) |
| `WEBHOOK_URL` | `http://localhost:5001/webhook` | Webhook URL for alert payloads |
| `ENABLE_MOBILE_NOTIFICATIONS` | `false` | Enable Twilio SMS notifications (`true`/`false`) |
| `TWILIO_ACCOUNT_SID` | *empty* | Twilio Account SID |
| `TWILIO_AUTH_TOKEN` | *empty* | Twilio Auth Token |
| `TWILIO_FROM_NUMBER` | *empty* | Twilio phone number |
| `TARGET_CAREGIVER_MOBILE` | *empty* | Recipient phone number for SMS alerts |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `DATA_LOG_PATH` | `data/behavior_log.csv` | Local CSV feature log destination |

---

## 3. Running AAMAS

### Live Camera Monitor

Start the local webhook receiver in one terminal:

```bash
python mock_server.py
```

Run the live monitor in another terminal:

```bash
# Default heuristic mode with live webcam and HUD:
python src/main.py

# Machine learning mode:
python src/main.py --mode ml --model-path models/stimming_classifier.pkl

# Run headlessly (no display window, suitable for background services):
python src/main.py --headless

# Run on a video file source with HUD:
python src/main.py --source samples/sample-2.mp4
```

#### HUD Controls & Keyboard Simulation

When running with a display window:
- `q`: Quit the application gracefully.
- `n`: Log current frame as **Normal** (label 0) to CSV.
- `s`: Log current frame as **Stimming** (label 1) to CSV.
- `a`: Log current frame as **Avoidance** (label 2) to CSV.
- `c`: Simulate covering ears (forces ear-coverage distance low).
- `e`: Simulate closing eyes (forces eye-openness ratio low).

---

### Recorded Video Ingest CLI

The recorded-video ingest CLI analyzes video files offline without opening a GUI window, logging skeletal metrics to CSV, capturing normalized skeleton snapshots, saving episodes to `data/outbox.db`, and dispatching alert events:

```bash
# Ingest a single video file:
python -m src.ingest samples/sample-2.mp4

# Ingest an entire directory of videos in batch:
python -m src.ingest samples/ --mode heuristic
```

---

### Standalone Web Dashboard & Skeleton Replay

Start the built-in HTTP server to access the single-file web dashboard and replay detected episodes on an animated HTML5 canvas:

```bash
# Start dashboard on port 8000:
python -m src.server --port 8000

# Specify custom database path or host:
python -m src.server --host 127.0.0.1 --port 8080 --db-path data/outbox.db
```

Navigate to `http://localhost:8000` to view the timeline, filter episodes by date/time/type, and inspect skeleton coordinate replays.

---

### Resetting Local State

To wipe the local SQLite database and start with a clean slate:

```bash
# Wipe local SQLite outbox database only (data/outbox.db):
./scripts/reset-local.sh

# Wipe both local database and CSV behavior logs:
./scripts/reset-local.sh --all
```

---

## 4. Machine Learning Model Training

To train the Random Forest stimming classifier using recorded CSV behavior logs:

```bash
# Train on collected data/behavior_log.csv:
python train_classifier.py --data data/behavior_log.csv --output models/stimming_classifier.pkl
```

---

## 5. Testing & Quality Assurance

Run the test suite:

```bash
# Run all unit tests
pytest

# Run tests with verbose output
pytest -v
```

Run linter checks:

```bash
ruff check .
```
