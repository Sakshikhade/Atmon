# AAMAS Demo Guide

This guide provides step-by-step instructions for demonstrating the core capabilities of the standalone AAMAS local edge monitor and video ingestion tool.

---

## What Is Demoable

| Demo | Feature | Focus |
| :---: | :--- | :--- |
| **Demo 1** | [Live Edge Camera Monitoring & HUD](#demo-1--live-edge-camera-monitoring--hud) | Real-time CV detection, neon HUD overlay, keyboard simulation |
| **Demo 2** | [Recorded Video Ingestion CLI](#demo-2--recorded-video-ingestion-cli) | Offline batch video processing, local CSV logging, alert triggering |
| **Demo 3** | [Standalone Web Dashboard & Skeleton Replay](#demo-3--standalone-web-dashboard--skeleton-replay) | Single-file UI, timeline filtering, HTML5 canvas skeleton player |
| **Demo 4** | [Machine Learning Model Training](#demo-4--machine-learning-model-training) | Training Random Forest classifier from CSV telemetry |
| **Demo 5** | [Multi-Channel Alert Gateway](#demo-5--multi-channel-alert-gateway) | Asynchronous webhook notifications and Twilio SMS |

---

## Environment Prerequisites

All commands are run from the project root inside an activated virtual environment:

```bash
# 1. Activate environment
source .venv/bin/activate

# 2. Verify dependencies
pip install -r requirements.txt

# 3. Configure local environment
cp .env.example .env
```

---

## Demo 1 — Live Edge Camera Monitoring & HUD

Demonstrates real-time on-device computer vision processing and visual HUD state indicators.

### Steps

1. **Start the local webhook receiver** in Terminal 1:
   ```bash
   python mock_server.py
   ```

2. **Launch the live monitor** in Terminal 2:
   ```bash
   python src/main.py
   ```

3. **Verify Heuristic Detection & Keyboard Simulation**:
   - **Normal State**: Stand in front of camera; HUD displays green bounding box and `NORMAL` state.
   - **Covering Ears Simulation**: Press and hold **`c`** on the keyboard.
     - Within ~1 second (30 frames), the HUD border turns **Amber** with banner `[!] AVOIDANCE DETECTED: EARS COVERED`.
     - Terminal 1 logs an `avoidance_start` webhook payload (`detail: ears_covered`).
     - Release key `c`; Terminal 1 logs an `avoidance_resolved` payload with duration.
   - **Closing Eyes Simulation**: Press and hold **`e`** on the keyboard.
     - Within ~1.5 seconds (45 frames), HUD turns **Amber** with banner `[!] AVOIDANCE DETECTED: EYES CLOSED`.
     - Terminal 1 logs `avoidance_start` (`detail: eyes_closed`), and `avoidance_resolved` upon release.
   - **Stimming Detection**: Perform rapid vertical arm movements (~4 Hz) for $\ge 3$ seconds.
     - HUD turns **Yellow** with banner `[!] STIMMING EVENT DETECTED`.
     - Terminal 1 logs `stimming_start` and `stimming_resolved` upon cessation.

4. **Press `q`** to quit the live window cleanly.

---

## Demo 2 — Recorded Video Ingestion CLI

Demonstrates headless offline analysis of pre-recorded video files.

### Steps

1. **Ingest a Single Video File**:
   ```bash
   python -m src.ingest samples/sample-2.mp4
   ```
   - Analyzes video frames headlessly at maximum processing speed.
   - Logs extracted telemetry features to `data/behavior_log.csv`.
   - Persists detected behavior episodes and privacy-preserving skeleton snapshots to `data/outbox.db`.
   - Dispatches start and resolution event alerts to the local webhook listener.

2. **Batch Directory Ingest**:
   ```bash
   python -m src.ingest samples/ --mode heuristic
   ```
   - Automatically iterates through all video files in `samples/`, summarizing processed frame counts, detected episodes, and saving snapshots to SQLite.

---

## Demo 3 — Standalone Web Dashboard & Skeleton Replay

Demonstrates the self-contained single-file HTML dashboard with interactive timeline and HTML5 canvas skeleton coordinate replay.

### Steps

1. **Start the Dashboard Server**:
   ```bash
   python -m src.server --port 8000
   ```

2. **Open Browser**:
   - Navigate to `http://localhost:8000`.
   - View top summary cards: Total Recorded Episodes, Today's Count, Rhythmic Stimming, and Avoidance Behaviors.

3. **Explore the Episode Timeline**:
   - Filter by date preset (All Time, Today, Yesterday) or specific behavior category (Stimming, Avoidance).
   - Click on any episode card in the left sidebar to inspect details and load its skeleton replay.

4. **Interact with the HTML5 Skeleton Player**:
   - Press **Play / Pause** (`⏸` / `▶`) to control animation playback.
   - Drag the **Scrubber Slider** or use **Step Frame** (`◀` / `▶`) to inspect individual landmark frames.
   - Toggle **Loop** or adjust playback speed (0.5x, 1.0x, 1.5x, 2.0x).
   - Verify that only normalized skeletal bones and color-coded joints are displayed with zero video/pixel storage.

---

## Demo 4 — Machine Learning Model Training

Demonstrates collecting labeled data and training the Random Forest classifier.

### Steps

1. **Data Collection during Monitoring**:
   - While running `python src/main.py`, hold down:
     - `n` to record Normal samples (Label 0).
     - `s` to record Stimming samples (Label 1).
     - `a` to record Avoidance samples (Label 2).
   - Press `q` to save rows to `data/behavior_log.csv`.

2. **Train the Random Forest Model**:
   ```bash
   python train_classifier.py --data data/behavior_log.csv --output models/stimming_classifier.pkl
   ```
   - Generates/evaluates feature vectors and outputs precision/recall metrics.
   - Serializes trained model to `models/stimming_classifier.pkl`.

3. **Run Monitor in Machine Learning Mode**:
   ```bash
   python src/main.py --mode ml --model-path models/stimming_classifier.pkl
   ```

---

## Demo 5 — Multi-Channel Alert Gateway

Demonstrates asynchronous notification routing via Webhooks and optional SMS.

### Steps

1. **Webhook Notification**:
   - With `mock_server.py` running, any triggered behavior dispatches an asynchronous HTTP POST payload containing:
     ```json
     {
       "event_type": "avoidance_start",
       "behavior": "avoidance",
       "detail": "ears_covered",
       "timestamp": "2026-09-01T23:40:00Z"
     }
     ```

2. **Twilio SMS Notification (Optional)**:
   - Configure credentials in `.env`:
     ```env
     ENABLE_MOBILE_NOTIFICATIONS=true
     TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
     TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
     TWILIO_FROM_NUMBER=+18005550199
     TARGET_CAREGIVER_MOBILE=+18005550100
     ```
   - On behavior trigger, AAMAS dispatches an SMS alert via the background worker queue within $< 2\text{ seconds}$ without dropping vision loop framerate.
