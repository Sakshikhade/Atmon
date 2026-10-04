# AAMAS — Functional FAQ

Plain-language answers to "how does the detection actually work?" — the questions a developer, caregiver, or reviewer asks about the standalone AAMAS local edge monitor. Grounded in the actual codebase.

For architecture and technical details, see the [Architecture document](architecture.md); for setup and usage, see the [Development Guide](development.md); for step-by-step demos, see the [Demo Guide](demo_guide.md).

> **Honest framing.** AAMAS is an engineering prototype that demonstrates local edge detection and real-time alerting. It has **not** been clinically validated and is **not** a medical diagnostic tool. Several answers below are deliberately candid about current capabilities and limitations.

---

### 1. What exactly does AAMAS detect?

Three behavioural states:
- **Normal** — baseline non-repetitive activity.
- **Stimming** — repetitive rhythmic motion such as arm/hand-flapping or rocking.
- **Physical Avoidance** — covering ears or closing eyes.

Plus a dedicated **ears-closed stimming** subtype (both hands on the ears *and* eyes closed simultaneously, sustained for $\ge 2.0\text{ seconds}$). Each detected episode records its type, start/end timestamps, and duration.

---

### 2. How does it "see" behaviour without recording video?

It uses **Google's MediaPipe Tasks API (`PoseLandmarker`)** to extract a 3D skeletal landmark model (positions of 33 key body joints including shoulders, wrists, eyes, ears) from each camera frame entirely in memory. As soon as coordinates are extracted, the video frame is explicitly purged from RAM (`del frame`). The system never saves raw images to disk or transmits pixels over the network; all detection works from numeric coordinate metrics alone. This is the foundation of the privacy-by-design architecture.

---

### 3. Is this an "AI model"? What kind?

AAMAS includes **two detection engines**:

- **Rule-based heuristic engine (the default):** Measures rhythm (FFT peak frequency) and velocity of wrist movements, applying explicit mathematical thresholds ($3.0\text{ Hz} - 6.5\text{ Hz}$, mean velocity $> 0.04$, distance standard deviation $> 0.01$). It runs out of the box with zero training required.
- **Optional machine learning engine:** A **Random Forest classifier** (scikit-learn, 100 trees) evaluating a 6-feature vector (`mean_distance`, `std_distance`, `mean_velocity`, `peak_frequency`, `mean_ear_coverage`, `mean_eye_openness`) smoothed across a 3.0-second sliding window. If no model file is found, it automatically falls back to the heuristic engine.

---

### 4. How does the rule-based engine decide something is "stimming"?

It analyzes rhythmic wrist motion in the **3.0–6.5 Hz range** (roughly 3–6 movements per second) with sufficient velocity and variance over a **3.0-second sliding window**. If this pattern holds for at least **50% of the window**, it flags a stimming state. A "possible stimming" warning appears on the HUD before the full threshold is reached.

---

### 5. How does it detect avoidance (covering ears / closing eyes)?

Through geometric calculations derived from skeletal landmarks:
- **Ears covered:** Normalized wrist-to-ear distance drops below `0.15` sustained for 30 consecutive frames (~1.0s at 30 FPS).
- **Eyes closed:** Scale-invariant eye-openness ratio drops below `0.20` sustained for 45 consecutive frames (~1.5s at 30 FPS).

---

### 6. What data is the machine-learning model trained on?

The training script (`train_classifier.py`) can generate synthetic samples based on kinematic distributions or train directly on recorded CSV telemetry captured in `data/behavior_log.csv` during live runs.

---

### 7. How fast is it? Is it real-time?

Yes. The vision loop runs at approximately **28–30 frames per second** on modern consumer hardware (tested on Apple Silicon). Hardware camera capture and alert dispatching are decoupled into dedicated worker threads, ensuring network I/O or Twilio SMS dispatches never stall the vision loop. Alerts are triggered within **1.0–1.5 seconds** of sustained behavior onset.

---

### 8. Can it analyse recorded video, or only a live camera?

Both. In addition to the live webcam monitor (`python src/main.py`), AAMAS includes a **recorded video ingestion CLI** (`python -m src.ingest samples/sample-2.mp4` or batch directory `samples/`) that runs the identical feature extraction and state tracking logic headlessly, logging CSV features, buffering skeleton snapshots, writing episodes to `data/outbox.db`, and dispatching alerts.

---

### 9. How do caregivers review past behavior episodes and skeleton replays?

Through the built-in **standalone web dashboard** (`python -m src.server --port 8000`). Opening `http://localhost:8000` in a browser allows caregivers and researchers to filter recorded episodes by date/time/behavior, view summary metrics, and inspect an animated HTML5 canvas playback of 3D skeletal joint movements without exposing any raw video.

---

### 10. What leaves the device, and what is stored?

- **Local Storage:** Metadata and normalized skeleton keypoint coordinates in `data/outbox.db`, plus optional CSV feature rows in `data/behavior_log.csv`.
- **Network Outbound:** Only structured, anonymized JSON metadata payloads (`event_type`, `timestamp`, `duration`, `behavior`, `detail`) sent to the configured `WEBHOOK_URL` and optional SMS text messages sent via Twilio.
- **Never Stored or Transmitted:** No raw video files, camera frames, or personal identifying information.
