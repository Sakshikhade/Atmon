"""Live camera capture and online grouping (spec 10).

Same encoder, same prototypes, same hysteresis as the offline path. What differs
is only that frames arrive in real time and an event's end is unknown when it
starts -- so the writer emits a provisional open row and a closed row later.

Two Phase 3 steps are absent here on purpose (spec 10.2): temporal NMS needs both
events complete, and cross-class resolution needs the full overlap picture.
Neither can run online, so live rows can overlap where offline rows would have
merged. Post-process the CSV offline if a merged view is needed -- do not add a
lookahead buffer here.
"""

import collections
import os
import signal
import threading
import time

import numpy as np

from src.clip_writer import FrameRetentionBuffer, build_store, clips_enabled, describe_store
from src.encoder import l2_normalize
from src.event_log import SOURCE_LIVE, EventLogWriter, live_session_id, new_run_id, utc_now
from src.normalize import RunningBackground
from src.scoring import HysteresisTracker, class_tau, similarity_to_class, stream_weights


def _resolve_pose_model_path(cfg):
    """Config-relative pose.model_path -> absolute, or None for default search."""
    mp = cfg.get("pose", {}).get("model_path") or None
    if mp and not os.path.isabs(mp):
        mp = os.path.normpath(os.path.join(cfg.root, mp))
    return mp


def load_live_pose_extractor(cfg):
    """DTW templates (pose.enabled) and/or geometry-only landmarker for ear gate.

    Full pose fusion stays behind pose.enabled. Classes with
    require_wrist_near_ear still get a PoseExtractor so live can confirm a
    wrist is near an ear without scoring the DTW stream.
    """
    from src.pose import PoseExtractor, classes_needing_wrist_gate, load_pose_templates

    templates = None
    extractor = None
    model_path = _resolve_pose_model_path(cfg)
    conf = cfg.get("pose", {}).get("min_confidence", 0.5)
    if cfg.get("pose", {}).get("enabled", False):
        templates = load_pose_templates(cfg)
        if templates:
            extractor = PoseExtractor(model_path=model_path, min_confidence=conf)
    elif classes_needing_wrist_gate(cfg):
        extractor = PoseExtractor(model_path=model_path, min_confidence=conf)
    return templates, extractor


class CameraStream:
    """Background capture. Drops frames rather than blocking the encoder.

    Backpressure is the normal state, not an error: V-JEPA 2 cannot keep up with
    a 30 fps camera, and we only need working_fps frames anyway. The drop rate is
    tracked so a pathological one is visible instead of silent.
    """

    def __init__(self, camera_index=0, capture_fps=30, drop_on_backpressure=True,
                 warmup_sec=5.0):
        import cv2

        self.capture = cv2.VideoCapture(camera_index)
        if not self.capture.isOpened():
            raise RuntimeError(
                "could not open camera %r.\n"
                "Run with --list-cameras to see what this machine exposes."
                % camera_index)
        self.capture.set(cv2.CAP_PROP_FPS, capture_fps)
        self._cv2 = cv2

        # A camera is not ready the instant it opens. Continuity Camera in
        # particular reports isOpened() while the iPhone is still waking, and
        # the first reads fail. Treating one failed read as end-of-stream ended
        # sessions after 0 chunks and 1.0s.
        self.warmup_sec = float(warmup_sec)
        self.camera_index = camera_index
        self.failure_reason = None
        self.drop_on_backpressure = drop_on_backpressure
        self.latest = None
        self.lock = threading.Lock()
        self.stopped = False
        self.captured = 0
        self.delivered = 0
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._loop, name="camera", daemon=True)
        self.thread.start()
        return self

    def _loop(self):
        deadline = time.time() + self.warmup_sec
        while not self.stopped:
            ok, frame = self.capture.read()
            if not ok:
                # Tolerate failures until the warm-up budget is spent. The clock
                # is reset by every successful frame, so a mid-session dropout
                # gets the same grace as start-up.
                if time.time() < deadline:
                    time.sleep(0.05)
                    continue
                self.failure_reason = (
                    "camera %r never delivered a frame within %.0fs -- it is most "
                    "likely a permissions issue, or Continuity Camera selecting an "
                    "iPhone that is asleep. Try --list-cameras, then --camera N."
                    % (self.camera_index, self.warmup_sec)
                    if self.captured == 0 else
                    "camera %r stopped delivering frames after %d frames"
                    % (self.camera_index, self.captured))
                self.stopped = True
                break
            deadline = time.time() + self.warmup_sec
            rgb = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB)
            with self.lock:
                self.captured += 1
                self.latest = rgb

    def read(self):
        """Newest frame, or None. Consuming clears it so nothing is used twice."""
        with self.lock:
            frame = self.latest
            self.latest = None
            if frame is not None:
                self.delivered += 1
            return frame

    @property
    def drop_rate(self):
        with self.lock:
            if self.captured == 0:
                return 0.0
            return 1.0 - (self.delivered / self.captured)

    def release(self):
        self.stopped = True
        if self.thread is not None:
            self.thread.join(timeout=1.0)
        self.capture.release()


def list_cameras(max_index=6, warmup_sec=2.0):
    """Probe camera indices and report which actually deliver frames.

    macOS exposes Continuity Camera (an iPhone) alongside the built-in one, and
    which lands on index 0 is not stable. A device can report isOpened() and
    still never produce a frame, so this checks delivery rather than opening.
    """
    import cv2

    found = []
    for index in range(max_index):
        capture = cv2.VideoCapture(index)
        if not capture.isOpened():
            capture.release()
            continue
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
        ok = False
        deadline = time.time() + warmup_sec
        while time.time() < deadline:
            ok, _ = capture.read()
            if ok:
                break
            time.sleep(0.05)
        capture.release()
        found.append({"index": index, "width": width, "height": height,
                      "fps": fps, "delivers": bool(ok)})
    return found


class LiveDetector:
    """Chunk the camera feed, score it, and drive one tracker per class."""

    def __init__(self, cfg, encoder, bank, tau_high, writer, on_event=None,
                 frame_buffer=None, clip_store=None, session_id=None,
                 pose_templates=None, hand_templates=None, background_scale=None):
        self.cfg = cfg
        self.encoder = encoder
        self.bank = bank
        self.writer = writer
        self.on_event = on_event or (lambda kind, event: None)

        # Clip capture is optional; without a buffer the detector behaves
        # exactly as before and retains no video at all.
        self.frame_buffer = frame_buffer
        self.clip_store = clip_store
        self.session_id = session_id
        clips_cfg = cfg.get("clips", {})
        self.pre_roll = float(clips_cfg.get("pre_roll_sec", 2.0))
        self.post_roll = float(clips_cfg.get("post_roll_sec", 2.0))

        self.class_names = sorted(bank.keys())
        self.chunk_sec = float(cfg["chunk_sec"])
        self.working_fps = float(cfg["working_fps"])
        self.frames_per_chunk = max(1, int(round(self.working_fps * self.chunk_sec)))

        live_cfg = cfg.get("live", {})
        # Live may tighten smoothing / window scales vs offline: long multi-scale
        # maxima keep scores high for seconds after the subject stops, so the
        # open row lingers and the close timestamp drifts late.
        if live_cfg.get("smoothing_windows") is not None:
            self.smoothing = int(live_cfg["smoothing_windows"])
        else:
            self.smoothing = int(cfg["smoothing_windows"])

        if live_cfg.get("window_scales") is not None:
            base = max(1, int(round(float(cfg["w_base_sec"]) / self.chunk_sec)))
            self.window_lengths = sorted({
                max(1, int(round(base * float(s)))) for s in live_cfg["window_scales"]
            })
        else:
            self.window_lengths = cfg.window_lengths_chunks()
        self.max_window = max(self.window_lengths)
        # Ring buffer holds just enough chunks for the longest window (spec 10.1).
        self.ring = collections.deque(maxlen=self.max_window)
        # Recent FUSED scores per class, for the moving average.
        self.recent = {name: collections.deque(maxlen=self.smoothing) for name in self.class_names}
        # While open, hysteresis close uses the shortest window only so the
        # detection ends when the action ends, not when a 6s lookback clears.
        self.close_on_short_window = bool(live_cfg.get("close_on_short_window", True))
        self.short_window = min(self.window_lengths)

        # Keypoint streams. Offline runs their per-chunk scores through the
        # embedding path's windowing; these rings are the online equivalent --
        # max over window lengths of the mean of the last W chunk scores, which
        # mirrors how self.ring is pooled for the appearance stream.
        self.pose_templates = pose_templates
        self.hand_templates = hand_templates
        self.stream_rings = {
            key: {name: collections.deque(maxlen=self.max_window) for name in self.class_names}
            for key in ("pose", "hands")
        }

        # Scores must be standardized against background before they can be
        # compared to tau_high, which calibrate.py produced in SIGMA. Offline
        # standardizes against the whole video; live cannot see the future, so
        # each stream keeps a running robust estimate instead. Until it has
        # warmed up no detection may open -- an unwarmed estimate produces
        # nonsense sigma for the first stretch of every session.
        self.warmup_chunks = int(live_cfg.get("background_warmup_chunks", 15))
        window = int(live_cfg.get("background_window_chunks", 300))
        # A calibrated scale applies to the appearance stream only -- that is
        # what calibrate.py measures. Streams without one fall back to
        # estimating spread from the running window.
        scales = background_scale or {}
        self.background_scale = scales
        self.background = {
            key: {name: RunningBackground(
                      window=window, warmup=self.warmup_chunks,
                      fixed_scale=scales.get(name) if key == "vjepa" else None)
                  for name in self.class_names}
            for key in ("vjepa", "pose", "hands")
        }
        if scales:
            # Only the centre needs estimating now, and a median converges in a
            # few samples rather than the minutes a spread would need.
            self.warmup_chunks = int(live_cfg.get("centre_warmup_chunks", 5))
            for key in self.background:
                for name in self.background[key]:
                    self.background[key][name].warmup = self.warmup_chunks

        # Offline smoothing is centered; online it can only look backwards, and a
        # trailing mean of N is the centered mean delayed by (N-1)//2 samples.
        # Uncompensated, every live event would be timestamped that much LATE --
        # a whole chunk, 2s at the shipped config. The delay is deterministic, so
        # scores are attributed to the span they actually describe: this deque
        # holds the last (delay + 1) chunk spans, and its oldest entry is the one
        # the current smoothed score is centered on.
        self.smoothing_delay_chunks = max(0, (self.smoothing - 1) // 2)
        self.span_history = collections.deque(maxlen=self.smoothing_delay_chunks + 1)

        max_open = cfg["event_log"].get("max_open_sec")
        # Per-class tau_scale (config classes.<name>) raises only that class's
        # bar -- used to cut hair_twirling FPs without touching head_nodding.
        self.trackers = {}
        for name in self.class_names:
            th, tl = class_tau(cfg, name, tau_high)
            confirm = float(cfg.class_cfg(name).get("confirm_sec", 0.0))
            self.trackers[name] = HysteresisTracker(
                name, th, tl, max_open_sec=max_open, confirm_sec=confirm)
        self.max_confirm_sec = max(
            (float(cfg.class_cfg(n).get("confirm_sec", 0.0)) for n in self.class_names),
            default=0.0,
        )

        # Live can pin a shorter floor so brief gestures (nods) are not discarded
        # when W_base is still a few seconds.
        if live_cfg.get("min_duration_sec") is not None:
            self.min_duration = float(live_cfg["min_duration_sec"])
        else:
            self.min_duration = float(cfg["min_duration_ratio"]) * float(cfg["w_base_sec"])
        self.n_chunks = 0

        live_flags = live_cfg
        self.cross_class = bool(live_flags.get("cross_class_resolution", True))
        self.cross_class_on_raw = bool(live_flags.get("cross_class_on_raw", True))
        # Near-tie opens are how nodding gets saved as ear_cover and how hair
        # fires on ambiguous face motion. Units follow the rank scale: cosine
        # when cross_class_on_raw, otherwise sigma above background.
        self.open_margin = float(live_flags.get(
            "open_margin", 0.02 if self.cross_class_on_raw else 0.35))
        self.protect_background = bool(live_flags.get("protect_background", True))
        self.mutual_exclusion = bool(live_flags.get("mutual_exclusion", True))
        # atmos-style geometric confirmation for classes that set
        # require_wrist_near_ear (ear_cover). Appearance-only often fires on
        # chin-rest / face-near motion; wrist-ear distance kills those FPs
        # without enabling the measured-harmful DTW pose fusion stream.
        self.wrist_gate_classes = {
            name for name in self.class_names
            if bool(cfg.class_cfg(name).get("require_wrist_near_ear", False))
        }
        self._last_wrist_near_ear = False

    @property
    def active_streams(self):
        streams = ["vjepa"]
        if self.pose_templates:
            streams.append("pose")
        if self.hand_templates:
            streams.append("hands")
        return streams

    @property
    def ready(self):
        """True once every active stream has a usable background estimate."""
        return all(self.background[k][n].ready
                   for k in self.active_streams for n in self.class_names)

    @property
    def lag_sec(self):
        """Reporting lag (spec 10.3).

        How late a row APPEARS, not how wrong its timestamps are. Capture time
        fixes the drift under load, and smoothing_delay_chunks compensates the
        causal filter's group delay, so start_sec/end_sec describe when the
        action actually happened; only the writing of the row is late.
        """
        stride = self.cfg.stride_chunks * self.chunk_sec
        return 0.5 * float(self.cfg["w_base_sec"]) + self.smoothing * stride

    def _window_scores(self, lengths=None):
        """Max over window scales of the top-k similarity, per class."""
        feats = np.stack(self.ring)
        lengths = list(lengths) if lengths is not None else self.window_lengths
        scores = {}
        for name in self.class_names:
            best = -np.inf
            for length in lengths:
                if len(feats) < length:
                    continue
                pooled = l2_normalize(feats[-length:].mean(axis=0)[None, :])
                sim = similarity_to_class(pooled, self.bank[name], self.cfg["topk_prototypes"])
                best = max(best, float(sim[0]))
            scores[name] = 0.0 if np.isneginf(best) else best
        return scores

    def _windowed(self, ring):
        """Max over window lengths of the mean of the last W chunk scores.

        The keypoint-stream analogue of pooling self.ring for the appearance
        stream, so all streams sit on the same temporal footing (spec 10.2).
        """
        values = list(ring)
        if not values:
            return 0.0
        best = -np.inf
        for length in self.window_lengths:
            if len(values) < length:
                continue
            best = max(best, float(np.mean(values[-length:])))
        return float(values[-1]) if np.isneginf(best) else best

    def _raw_stream_scores(self, pose_seq, hand_seq):
        """Per-class raw score for every active stream, before standardizing."""
        from src.pose import similarity as pose_similarity

        raw = {"vjepa": self._window_scores()}

        for key, templates, seq in (("pose", self.pose_templates, pose_seq),
                                    ("hands", self.hand_templates, hand_seq)):
            if not templates:
                continue
            temperature = float(self.cfg.get(key, {}).get("temperature", 0.35))
            per_class = {}
            for name in self.class_names:
                class_templates = templates.get(name, [])
                value = 0.0
                if class_templates and seq is not None:
                    value = pose_similarity(seq, class_templates, temperature)
                self.stream_rings[key][name].append(value)
                per_class[name] = self._windowed(self.stream_rings[key][name])
            raw[key] = per_class
        return raw

    def push_chunk(self, feature, start_sec, end_sec=None, pose_seq=None, hand_seq=None):
        """Feed one encoded chunk. Writes any rows its arrival triggers.

        start_sec/end_sec are the chunk's MEASURED span, taken from when its
        frames were actually captured -- not a chunk counter times chunk_sec.
        On hardware that cannot encode as fast as the camera delivers, those two
        diverge, and a counter would report every event as having happened
        earlier than it did (see run_live).

        end_sec defaults to the nominal span, which is correct only when the
        pipeline is keeping up.
        """
        self.ring.append(feature)
        self.n_chunks += 1
        if end_sec is None:
            end_sec = start_sec + self.chunk_sec

        # Attribute the smoothed score to the span it is centered on, not to the
        # newest chunk. Before enough history exists the oldest entry IS the
        # current span, so early chunks are simply uncompensated.
        self.span_history.append((start_sec, end_sec))
        effective_start, effective_end = self.span_history[0]

        raw = self._raw_stream_scores(pose_seq, hand_seq)
        active = self.active_streams

        if self.wrist_gate_classes:
            from src.pose import wrist_near_ear

            # pose_seq is expected already normalize_pose()'d by the caller.
            thr = 0.28
            for name in self.wrist_gate_classes:
                thr = float(self.cfg.class_cfg(name).get(
                    "wrist_near_ear_threshold", thr))
                break
            self._last_wrist_near_ear = bool(
                pose_seq is not None and wrist_near_ear(pose_seq, threshold=thr))
        else:
            self._last_wrist_near_ear = False

        # Pass 1: score every class. Openings cannot be decided per class in
        # isolation -- cross-class resolution needs all of them.
        #
        # Open path: multi-scale max (recall). Close path: shortest window only,
        # so a detection drops as soon as the latest chunk no longer matches --
        # otherwise a 3-chunk lookback keeps the event open for ~W_base after
        # the person already stopped.
        scores_open = {}
        scores_close = {}
        raw_short = None
        if self.close_on_short_window:
            raw_short = dict(raw)
            raw_short["vjepa"] = self._window_scores(lengths=[self.short_window])

        for name in self.class_names:
            in_event = self.trackers[name].is_open
            zs = {}
            zs_close = {}
            for key in active:
                tracker = self.background[key][name]
                # Do NOT feed a chunk that is inside this class's own detection
                # back into its background estimate. Measured, a sustained event
                # drags the running median up into itself and hysteresis closes
                # early -- a 68s event was truncated at 34s, tIoU 0.49 against
                # 0.96 offline.
                if not (in_event and self.protect_background):
                    tracker.update(raw[key][name])
                zs[key] = tracker.normalize(raw[key][name])
                if raw_short is not None:
                    zs_close[key] = tracker.normalize(raw_short[key][name])

            weights = stream_weights(self.cfg, name, active)
            fused = sum(weights[k] * zs[k] for k in active)
            self.recent[name].append(fused)
            scores_open[name] = float(np.mean(self.recent[name]))
            if raw_short is not None:
                fused_close = sum(weights[k] * zs_close[k] for k in active)
                scores_close[name] = float(fused_close)
            else:
                scores_close[name] = scores_open[name]

        scores = scores_open

        # No detection may open on an unwarmed background estimate.
        if not self.ready:
            self._update_retention_floor()
            return

        # Pass 2: cross-class resolution (spec 7.3.5), online.
        #
        # Offline this runs after grouping, deleting the loser wherever classes
        # overlap. Online there is no "after", but the same rule applies at the
        # moment of opening: a class may open only if nothing else already
        # open, or opening in this same step, scores higher. Measured live, this
        # was three of five detections -- one span reported as BOTH classes, and
        # an ear-cover simultaneously reported as hair-twirling.
        #
        # An incumbent is never closed early by a newcomer: truncating a real
        # event is worse than tolerating the overlap, so live can still emit one
        # where offline would not.
        #
        # Rank scale: RAW cosine is stable across classes but face-near actions
        # (ear / hair / nod) sit within ~0.05 of each other, so a nod is often
        # awarded to ear_cover. Ranking on per-class z (sigma) after each class
        # has cleared its own tau prefers the class that is actually unusual vs
        # its background. open_margin suppresses near-ties either way.
        rank = raw["vjepa"] if self.cross_class_on_raw else scores
        contenders = {n: rank[n] for n in self.class_names
                      if self.trackers[n].is_open or scores[n] > self.trackers[n].tau_high}
        best = max(contenders.values()) if contenders else None
        if contenders and len(contenders) >= 2:
            ordered = sorted(contenders.values(), reverse=True)
            ambiguous = (ordered[0] - ordered[1]) < self.open_margin
        else:
            ambiguous = False

        # Classes closed by mutual exclusion in THIS chunk must not reopen in
        # the same pass -- otherwise A opens, closes B, then B's still-above-
        # threshold score reopens B immediately (false handoff churn).
        closed_by_exclusion = set()

        for name in self.class_names:
            # Ambiguous near-ties: do not open anyone new. Incumbents may stay
            # open and still close on the short-window path below.
            clear_winner = (best is not None and rank[name] >= best - 1e-12
                            and not ambiguous)
            allow_open = (not self.cross_class) or clear_winner
            if name in closed_by_exclusion:
                allow_open = False
            # Geometric confirmation (atmos hand-to-ear): appearance may match
            # ear_cover on a chin rest; without a wrist near an ear, refuse open.
            if (allow_open and name in self.wrist_gate_classes
                    and not self.trackers[name].is_open
                    and not self._last_wrist_near_ear):
                allow_open = False

            # Mutual exclusion: one subject cannot be doing two of these at once,
            # so a new detection is evidence the running one has ended. Measured
            # on the recorded sessions, every runaway had another detection
            # firing inside it -- a 106s ear_cover with hair_twirling opening at
            # 52s, a 102s hair_twirling with ear_cover opening at 109s.
            #
            # A domain assumption, not a general truth: two hands can do two
            # things, and it gets weaker as classes are added. Hence the flag.
            # Require a clear margin over the incumbent so a 0.01 cosine flicker
            # cannot flip ear_cover → hair_twirling mid-event.
            if (self.mutual_exclusion and allow_open
                    and not self.trackers[name].is_open
                    and scores[name] > self.trackers[name].tau_high):
                for other in self.class_names:
                    if other != name and self.trackers[other].is_open:
                        if rank[name] < rank[other] + self.open_margin:
                            continue
                        ended = self.trackers[other].flush(effective_start)
                        if ended:
                            ended["superseded_by"] = name
                            self._close(ended)
                            closed_by_exclusion.add(other)

            # Open decisions use multi-scale smoothed scores; close decisions
            # use the short-window score so end times track the real stop.
            step_score = scores_close[name] if self.trackers[name].is_open else scores[name]
            # When closing on the latest chunk, stamp with the current span --
            # the delayed span is for the smoothed open path only.
            step_start, step_end = (
                (start_sec, end_sec) if self.trackers[name].is_open and self.close_on_short_window
                else (effective_start, effective_end)
            )
            opened, closed = self.trackers[name].step(
                step_start, step_end, step_score, allow_open=allow_open)
            if opened:
                self.writer.open_event(opened["event_id"], name, opened["start"], opened["score"])
                self.on_event("open", opened)
            if closed:
                self._close(closed)

        self._update_retention_floor()

    def _update_retention_floor(self):
        """Hold frames back only while some detection still needs them.

        With nothing open the buffer keeps just its base window; the floor is
        the earliest open event's start, minus pre-roll.
        """
        if self.frame_buffer is None:
            return
        open_starts = [t.start_sec for t in self.trackers.values() if t.is_open]
        self.frame_buffer.set_floor(min(open_starts) - self.pre_roll if open_starts else None)

    def _write_clip(self, event):
        """Cut this event's clip out of the retention buffer."""
        if self.frame_buffer is None or self.clip_store is None:
            return None

        start = max(0.0, event["start"] - self.pre_roll)
        end = min(event["end"] + self.post_roll, start + self.clip_store.max_clip_sec)
        frames = self.frame_buffer.slice(start, end)
        if not frames:
            return None

        from src.clip_writer import touch, write_frames

        path = self.clip_store.path_for(self.session_id or "live", event["event_id"])
        written = write_frames(path, frames, self.cfg["working_fps"])
        if written:
            touch(written)
            return self.clip_store.relative(written)
        return None

    def _close(self, event):
        # Minimum duration still applies, but only at close: a short event's open
        # row is already on disk and stays as its only trace (spec 10.2).
        if (event["end"] - event["start"]) < self.min_duration:
            self.on_event("discarded", event)
            self._update_retention_floor()
            return

        clip_path = self._write_clip(event)
        event["clip_path"] = clip_path
        self.writer.close_event(
            event["event_id"], event["class"], event["start"], event["end"], event["score"],
            clip_path=clip_path,
        )
        self.on_event("close", event)
        self._update_retention_floor()

    def flush(self, end_sec):
        """Close every open detection. Called on clean shutdown (spec 10.4)."""
        for name in self.class_names:
            closed = self.trackers[name].flush(end_sec)
            if closed:
                self._close(closed)


def run_live(cfg, encoder, bank, tau_high, on_event=None, max_seconds=None):
    """Capture until SIGINT (or max_seconds), detecting as frames arrive."""
    started_at = utc_now()
    session_id = live_session_id(started_at)

    detector_writer = EventLogWriter(
        path=cfg.path(cfg["event_log"]["path"]),
        run_id=new_run_id(),
        source_id=session_id,
        source_type=SOURCE_LIVE,
        model_id=cfg["model_id"],
        working_fps=cfg["working_fps"],
        chunk_sec=cfg["chunk_sec"],
        tau_high=tau_high,
        source_start_utc=started_at,          # live always knows its origin
        flush_each_event=bool(cfg["event_log"]["flush_each_event"]),
    )

    live_cfg = cfg["live"]
    stream = CameraStream(
        camera_index=live_cfg["camera_index"],
        capture_fps=live_cfg["capture_fps"],
        drop_on_backpressure=live_cfg["drop_on_backpressure"],
        warmup_sec=float(live_cfg.get("camera_warmup_sec", 5.0)),
    ).start()

    # Same streams as the offline path. Without these, live would score raw
    # cosine while tau_high is in sigma -- a unit mismatch under which nothing
    # can ever fire.
    pose_templates, pose_extractor = load_live_pose_extractor(cfg)
    hand_templates = hand_extractor = None
    if cfg.get("hands", {}).get("enabled", False):
        from src.hands import HandExtractor, load_hand_templates

        hand_templates = load_hand_templates(cfg)
        if hand_templates:
            hand_extractor = HandExtractor(
                model_path=cfg["hands"].get("model_path") or None,
                min_confidence=cfg["hands"].get("min_confidence", 0.4),
            )

    # Calibrated scale is OFF by default. It measured well on eval3 (mAP 0.850
    # -> 0.917) but the value calibrate.py produced was wrong in practice: it
    # pooled raw scores across videos whose centres differ by 0.29, so it
    # measured the gap BETWEEN scenes rather than background variation within
    # one, and came out ~3x too large. Live then detected nothing at all.
    #
    # calibrate.py now aggregates per video, so the stored value is correct --
    # but the mechanism stays behind a flag until it has been shown to work on a
    # real session, not just in simulation.
    background_scale = None
    if bool(cfg.get("live", {}).get("use_calibrated_scale", False)):
        sidecar = cfg.path("cache", "calibration.json")
        if os.path.exists(sidecar):
            import json

            with open(sidecar, "r", encoding="utf-8") as fh:
                background_scale = json.load(fh).get("background_scale")

    detector = LiveDetector(
        cfg, encoder, bank, tau_high, detector_writer, on_event, session_id=session_id,
        pose_templates=pose_templates, hand_templates=hand_templates,
        background_scale=background_scale,
    )
    if background_scale:
        print("bg scale  : calibrated %s (warmup %d chunks -- centre only)"
              % ({k: round(v, 4) for k, v in background_scale.items()}, detector.warmup_chunks))
    else:
        print("bg scale  : ESTIMATED LIVE -- run calibrate.py; a short window "
              "underestimates spread ~6x and inflates every score")

    # Clip capture (optional). The buffer retains only what a detection could
    # still need: the reporting lag plus pre-roll, extended while an event is
    # open, and hard-capped so one stuck detection cannot grow it without end.
    frame_buffer = None
    if clips_enabled(cfg):
        clips_cfg = cfg["clips"]
        store = build_store(cfg)
        base_horizon = detector.lag_sec + detector.pre_roll + detector.max_confirm_sec + 2.0
        frame_buffer = FrameRetentionBuffer(
            base_horizon_sec=base_horizon,
            hard_ceiling_sec=store.max_clip_sec + detector.pre_roll + detector.post_roll + detector.lag_sec + detector.max_confirm_sec,
            jpeg_quality=clips_cfg.get("jpeg_quality", 80),
        )
        detector.frame_buffer = frame_buffer
        detector.clip_store = store
        print("clips     : %s" % describe_store(store))
        print("            retaining ~%.0fs of frames in memory (JPEG) so clips can" % base_horizon)
        print("            include the seconds before a detection is recognised")
    else:
        store = None
        print("clips     : disabled (timestamps only, no video retained)")

    stopping = {"flag": False}

    def handle_sigint(signum, frame):  # noqa: ARG001
        stopping["flag"] = True

    previous = signal.signal(signal.SIGINT, handle_sigint)

    print("session   : %s" % session_id)
    print("classes   : %s" % ", ".join(detector.class_names))
    print("streams   : %s" % " + ".join(detector.active_streams))
    print("tau_high  : %.4f sigma above background" % tau_high)
    print("warmup    : %d chunks (%.0fs) before any detection can open"
          % (detector.warmup_chunks, detector.warmup_chunks * detector.chunk_sec))
    print("report lag: ~%.1fs (timestamps themselves are not lagged -- spec 10.3)" % detector.lag_sec)
    print("Ctrl-C to stop.\n")

    t0 = time.time()
    frame_interval = 1.0 / float(cfg["working_fps"])
    next_frame_at = t0
    buffer = []                  # (capture_sec, frame), capture_sec relative to t0
    chunk_index = 0
    encode_total = 0.0
    behind_warned = False

    try:
        while not stopping["flag"]:
            if max_seconds is not None and (time.time() - t0) >= max_seconds:
                break
            if stream.stopped:
                print("\n%s" % (stream.failure_reason or "camera stopped delivering frames"))
                break

            now = time.time()
            if now < next_frame_at:
                time.sleep(min(0.005, next_frame_at - now))
                continue

            frame = stream.read()
            if frame is None:
                time.sleep(0.002)
                continue

            capture_sec = now - t0
            buffer.append((capture_sec, frame))
            if frame_buffer is not None:
                frame_buffer.append(capture_sec, frame)
            next_frame_at += frame_interval
            # Encoding a chunk can take longer than the frames it covers. When it
            # does, the schedule is already in the past, and sprinting to refill
            # a backlog would just collect frames that no longer correspond to
            # anything the camera is showing. Resume pacing from now instead.
            if next_frame_at < now:
                next_frame_at = now + frame_interval

            if len(buffer) >= detector.frames_per_chunk:
                taken = buffer[: detector.frames_per_chunk]
                buffer = buffer[detector.frames_per_chunk :]

                # The chunk's real span, from when its frames were captured.
                start_sec = taken[0][0]
                end_sec = taken[-1][0] + frame_interval

                clip = np.stack([f for _, f in taken])
                encode_started = time.time()
                feature = encoder.encode_clips([clip])[0]
                pose_seq = hand_seq = None
                if pose_extractor is not None:
                    from src.pose import normalize_pose

                    pose_seq = normalize_pose(pose_extractor.landmarks_for_clip(clip))
                if hand_extractor is not None:
                    from src.hands import normalize_hand

                    hand_seq = normalize_hand(hand_extractor.landmarks_for_clip(clip))
                encode_sec = time.time() - encode_started
                encode_total += encode_sec

                detector.push_chunk(feature, start_sec, end_sec, pose_seq, hand_seq)
                chunk_index += 1
                if chunk_index == detector.warmup_chunks:
                    print("  background estimate warmed up; detection is now live\n")

                if not behind_warned and encode_sec > (end_sec - start_sec):
                    behind_warned = True
                    print(
                        "\n  WARNING: encoding a chunk took %.1fs but the chunk covers only"
                        "\n  %.1fs of video. Timestamps stay truthful -- they come from capture"
                        "\n  time -- but chunks now span uneven, longer stretches than chunk_sec,"
                        "\n  and window pooling assumes uniform spacing, so scoring is distorted."
                        "\n  Lower frames_per_clip, or raise chunk_sec so a chunk covers more"
                        "\n  video per encode.\n" % (encode_sec, end_sec - start_sec)
                    )
    finally:
        signal.signal(signal.SIGINT, previous)
        for extractor in (pose_extractor, hand_extractor):
            if extractor is not None:
                extractor.close()
        # A clean stop closes open detections normally -- a real closed row, not
        # a force-close (spec 10.4). End at measured elapsed time, not a counter.
        detector.flush(time.time() - t0)
        detector_writer.close()
        drop_rate = stream.drop_rate
        stream.release()
        if store is not None:
            deleted, freed = store.enforce_budget()
            if deleted:
                print("\nclip retention: deleted %d old clip(s), freed %.2f GB"
                      % (deleted, freed / (1024 ** 3)))
        if frame_buffer is not None:
            frame_buffer.clear()

    elapsed = time.time() - t0
    print("\nsession %s ended after %d chunks (%.1fs)" % (session_id, chunk_index, elapsed))
    print("camera frame drop rate: %.1f%%" % (drop_rate * 100.0))
    if store is not None:
        print("clips                 : %s" % describe_store(store))
    if chunk_index:
        mean_encode = encode_total / chunk_index
        factor = mean_encode / float(cfg["chunk_sec"])
        print("mean encode time      : %.2fs per chunk (%.2fx real time)" % (mean_encode, factor))
        if factor > 1.0:
            print("  the pipeline ran behind the camera; see the warning above")
    return session_id
