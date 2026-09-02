# Autism Activity Monitoring & Alerting System (AAMAS)
## Complete Test Plan & Verification Protocol

This document defines the testing strategy, test cases, and execution protocols to validate the accuracy, latency, performance, stability, and privacy of the standalone AAMAS edge monitor and video ingestion tool.

---

## 1. Objectives & Quality Benchmarks

The testing strategy is designed to verify that the AAMAS local monitor meets the core system requirements defined in the [Product Scope](product-scope.md):

*   **Detection Accuracy:** $\ge 85\%$ precision and recall on targeted repetitive behaviors.
*   **Pipeline Latency:** $\le 1.5$ seconds elapsed from threshold-exceeding behavior start to alert dispatch.
*   **System Throughput:** $\ge 30$ FPS (target) and $\ge 15$ FPS (minimum constraint) on standard consumer-grade CPUs.
*   **Stability / Resource Management:** Zero memory leaks, thread lockups, or crashes during a continuous $4$-hour stress test.
*   **Privacy-by-Design Compliance:** Verifiably zero raw video/image frames written to local storage, and zero personal identifiers sent via network payloads.

---

## 2. Test Environment Configuration

*   **Hardware:** Desktop/Laptop with webcam support (built-in or USB external).
*   **Runtime:** Python 3.10+ (developed and tested on Python 3.11) inside `.venv`.
*   **Key Libraries:** `opencv-python`, `mediapipe`, `numpy`, `scikit-learn`, `requests`, `twilio`.
*   **External Listener:** Local webhook receiver (`mock_server.py`) running on `localhost:5001`.

---

## 3. Test Cases (Granular Specification)

### 3.1 Unit Testing (UT)

#### UT-001: Skeletal Distance Normalization
*   **Objective:** Validate that coordinate tracking is scale-invariant.
*   **Test Setup:** Supply dummy coordinates mimicking a subject standing close to the camera versus far away.
*   **Verification Criteria:** Wrist-to-shoulder distance normalized by shoulder width must yield identical values ($\pm 0.01$).
*   **Automated by:** `tests/test_feature_extractor.py::test_normalization_logic`.

#### UT-002: Temporal Peak-Finding Frequency Calculation
*   **Objective:** Verify that classifier rhythm analysis accurately computes cycle rates.
*   **Test Setup:** Inject a synthetic $4.0\text{ Hz}$ sinusoidal distance signal into `BehaviorClassifier.update()` over 120 frames (4 seconds at 30 FPS).
*   **Verification Criteria:** The computed `frequency` output must match the expected $4.0\text{ Hz}$ within $\pm 0.5\text{ Hz}$.
*   **Automated by:** `tests/test_classifier.py::test_heuristic_frequency_calculation`.

#### UT-003: Sliding Activation Window & Hysteresis Logic
*   **Objective:** Verify the sliding threshold trigger ($50\%$ match ratio over 3.0s window) and window maxlen resizing on FPS changes.
*   **Test Setup:** Feed matching stimming frames to fill the activation deque, then feed non-matching frames.
*   **Verification Criteria:**
    *   `is_stimming` flips to `True` when ratio $\ge 0.50$ and minimum frame count is met.
    *   `is_stimming` flips to `False` once ratio drops below threshold.
    *   Window maxlen updates to `int(activation_window_duration * fps)` while preserving history.
*   **Automated by:** `tests/test_classifier.py::test_stimming_trigger`, `test_non_stimming_behavior`, and `test_heuristic_fps_update_preserves_history`.

#### UT-004: Ears-Closed Stimming Detection
*   **Objective:** Validate `EarsClosedStimmingDetector` (both hands near ears + eyes closed sustained for $\ge 2.0\text{ seconds}$).
*   **Test Setup & Verification Criteria:**
    1. **Sustained trigger:** Both wrists near ears and low `eye_openness_ratio` held for $\ge 60\text{ frames}$ at 30 FPS.
    2. **One hand not near ear:** Signal remains `False`.
    3. **Eyes open:** Signal remains `False`.
*   **Automated by:** `tests/test_ears_closed_stimming.py`.

#### UT-005: Ear Coverage Distance Calculation
*   **Objective:** Validate normalized ear coverage distance calculation using wrist and ear coordinates scaled by shoulder width.
*   **Automated by:** `tests/test_feature_extractor.py::test_ear_coverage_distance_low_when_hands_near_ears`, `test_ear_coverage_distance_high_when_hands_away_from_ears`.

#### UT-006: Eye Openness Ratio Calculation
*   **Objective:** Validate scale-invariant eye openness ratio calculation and simulation override flags.
*   **Automated by:** `tests/test_feature_extractor.py::test_eye_openness_ratio_high_when_eyes_open`, `test_eye_openness_ratio_low_when_eyes_closed`, `test_simulation_overrides_force_avoidance_values`.

#### UT-007: ML Wrapper Fallback & Schema
*   **Objective:** Verify `MLBehaviorClassifier` falls back to heuristic engine when model is absent, emits expected schema, and resizes window on FPS change.
*   **Automated by:** `tests/test_ml_classifier.py`.

---

### 3.2 Integration Testing (IT)

#### IT-001: Asynchronous Webhook Alert Dispatch
*   **Objective:** Validate non-blocking webhook dispatch, payload formatting, and resilience to server connection errors.
*   **Automated by:** `tests/test_alert_gateway.py::test_alert_gateway_webhook_post_on_send_alert`, `test_alert_gateway_payload_shape`, `test_alert_gateway_webhook_failure_does_not_raise`.

#### IT-002: Mobile SMS Routing & Graceful Fallback
*   **Objective:** Validate Twilio SMS dispatch when enabled and graceful fallback warning when credentials are absent.
*   **Automated by:** `tests/test_alert_gateway.py::test_alert_gateway_sms_dispatch_when_enabled`, `test_alert_gateway_sms_graceful_fallback_on_missing_credentials`.

#### IT-003: Threaded Camera Stream Robustness
*   **Objective:** Verify non-blocking camera frame caching, transient failure recovery, and clean EOF handling for video files.
*   **Automated by:** `tests/test_camera_stream.py`.

#### IT-004: Shared Pipeline Event Tracking
*   **Objective:** Verify `EventTracker` start and resolution edge dispatch, duration tracking, scale-invariant distance calculation, and multi-behavior independence.
*   **Automated by:** `tests/test_pipeline.py`.

#### IT-005: Recorded Video Ingestion CLI
*   **Objective:** Verify single-file and directory-batch video ingestion, local SQLite storage, local CSV feature logging, and alert dispatch.
*   **Automated by:** `tests/test_ingest.py`.

#### IT-006: Local SQLite Event Storage & Skeleton Snapshots
*   **Objective:** Verify SQLite database table creation, atomic event and snapshot insertion, filtered queries by date/time/type/source, snapshot JSON parsing, stats aggregation, and cascade deletion.
*   **Automated by:** `tests/test_event_storage.py`.

#### IT-007: Standalone Dashboard Server & REST API
*   **Objective:** Verify HTTP server root HTML serving, REST API endpoints (`/api/stats`, `/api/episodes`, `/api/episodes/<id>`), and 404 handling.
*   **Automated by:** `tests/test_server.py`.

---

### 3.3 End-to-End Functional Testing (E2E)

| Test ID | Test Scenario | Input / Action | Expected Result | Status |
| :--- | :--- | :--- | :--- | :---: |
| **E2E-001** | **Positive Stimming Trigger** | Repetitive arm flapping (3.0 Hz to 6.5 Hz) for $\ge 3$ seconds. | HUD draws yellow border, displays `[!] STIMMING EVENT DETECTED`, and `mock_server` prints a `stimming_start` JSON. | Pass |
| **E2E-002** | **Negative Gesture Safety** | Slow movements, random static postures, or normal gestures. | Status remains `NORMAL` without triggering alert; 0 webhooks dispatched. | Pass |
| **E2E-003** | **Tracking Recovery** | Step out of camera field of view for $\ge 2$ seconds, then re-enter. | System detects tracker loss, resets deques, status sets to `NORMAL`, and resumes tracking seamlessly upon re-entry. | Pass |
| **E2E-004** | **ML Classifier Mode** | Run monitor using `python src/main.py --mode ml`. | Model loaded, telemetry processed at ~30 FPS, alerts triggered on matching behavior. | Pass |
| **E2E-005** | **Keyboard Data Recording** | Hold down key `n` or `s` in camera window. | Red recording badge displays on HUD, and frames log to `data/behavior_log.csv`. | Pass |
| **E2E-006** | **Covering Ears Simulation** | Hold down key `c` in camera window. | HUD border turns amber, prints `[!] AVOIDANCE DETECTED: EARS COVERED`, and mock_server logs `avoidance_start` webhook. | Pass |
| **E2E-007** | **Closing Eyes Simulation** | Hold down key `e` in camera window. | HUD border turns amber, prints `[!] AVOIDANCE DETECTED: EYES CLOSED`, and mock_server logs `avoidance_start` webhook. | Pass |
| **E2E-008** | **Avoidance Data Recording** | Hold down key `a` in camera window. | HUD border turns amber, flashes `REC: AVOIDANCE` badge, and logs frames (Label 2) to `data/behavior_log.csv`. | Pass |
| **E2E-009** | **Offline Video Ingest** | Run `python -m src.ingest samples/sample-2.mp4`. | Video is ingested headlessly at high speed, logs features to CSV, and dispatches detected events. | Pass |

---

### 3.4 Performance & Stability Testing (PT)

#### PT-001: 4-Hour Stability & Resource Leak Test
*   **Objective:** Validate that the system does not crash or suffer memory leaks over prolonged use.
*   **Method:** Run the system pointing at a stationary scene/object for 4 consecutive hours.
*   **Verification Criteria:** The resident memory size (RSS) of the Python process remains stable ($\pm 10\text{ MB}$).

#### PT-002: Frame Rate and Dispatch Lag Verification
*   **Objective:** Confirm asynchronous alerting preserves the stream frame rate.
*   **Method:** Profile loop latency inside `src/main.py`.
*   **Verification Criteria:** The active framerate maintains $\ge 28\text{ FPS}$ during webhook dispatches. Webhook receipt latency remains under $100\text{ ms}$ locally.

---

## 4. Manual Verification Run-Book

### Step 1: Initialize Local Webhook Receiver
```bash
python mock_server.py
```
Verify the listener starts on port 5001.

### Step 2: Launch AAMAS Core Monitor
```bash
python src/main.py
```
Verify the webcam window opens and displays the neon stats HUD overlay in the top left.

### Step 3: Verify Keyboard Simulations
- Hold **`c`** to simulate covering ears (HUD turns amber, `avoidance_start - ears_covered` dispatched).
- Hold **`e`** to simulate closing eyes (HUD turns amber, `avoidance_start - eyes_closed` dispatched).
- Release keys to verify `avoidance_resolved` dispatches.

### Step 4: Verify Video Ingest CLI
```bash
python -m src.ingest samples/sample-2.mp4
```
Verify terminal output summarizes processed frames and detected events.

---

## 5. Automated Testing Summary

AAMAS includes **45 automated unit and integration tests** across 10 test modules. None require a physical webcam.

### Running Tests
```bash
# Run test suite
pytest

# Verbose output
pytest -v

# Run linter
ruff check .
```

### Test Matrix

| Test Module | Test Count | Scope |
| :--- | :---: | :--- |
| `tests/test_feature_extractor.py` | 6 | Scale-invariant normalization, ear coverage distance, eye openness ratio, simulation overrides |
| `tests/test_classifier.py` | 4 | Heuristic peak-counting frequency math, threshold triggers, activation window resizing |
| `tests/test_ml_classifier.py` | 3 | ML wrapper heuristic fallback, output schema parity, window resizing |
| `tests/test_ears_closed_stimming.py` | 3 | Both-hands-on-ears + eyes-closed sustained detection |
| `tests/test_camera_stream.py` | 3 | Threaded non-blocking frame cache, transient failure retry, video EOF handling |
| `tests/test_alert_gateway.py` | 5 | Asynchronous worker queue, webhook payload shape, error resilience, Twilio SMS routing, credential fallback |
| `tests/test_pipeline.py` | 8 | Shared `EventTracker` transitions, durations, scale-invariant distance, multi-behavior independence |
| `tests/test_event_storage.py` | 4 | SQLite CRUD operations, date/time filtering, stats calculation, snapshot downsampling |
| `tests/test_server.py` | 4 | REST API endpoints (`/api/stats`, `/api/episodes`, `/api/episodes/<id>`), and HTML dashboard serving |
| `tests/test_ingest.py` | 5 | Recorded video ingestion CLI, directory batch processing, SQLite storage, CSV logging, alert dispatch |
| **Total** | **45** | **100% Automated Test Suite Passing** |
