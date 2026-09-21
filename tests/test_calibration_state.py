"""A demo preset must never be able to present itself as calibrated.

The original failure was four lines in load_config: it read tau_high out of
cache/calibration.json and then set _tau_high_source to the bare FILENAME,
throwing away the sidecar's own "source" field. The UI rendered
"tau 1.500 - cache/calibration.json", which reads as file-backed and measured.
The file said "demo-preset (uncalibrated, see spec 8)" the whole time.

The second failure was the webapp WRITING that preset into the sidecar whenever
nothing was calibrated -- turning "we have no threshold" into a file that every
later reader, require_tau_high included, treated as an answer.
"""

import json
import os
import tempfile

import pytest

from src.config import CALIBRATION_PATH, calibration_status, load_config

CONFIG_YAML = """
model_id: stub-encoder-v1
working_fps: 8
chunk_sec: 1.0
window_scales: [0.7, 1.0, 1.4]
stride_ratio: 0.25
topk_prototypes: 3
smoothing_windows: 3
tau_high: null
tau_low_ratio: 0.85
min_duration_ratio: 0.5
nms_tiou: 0.5
max_false_alarms_per_hour: 5
frames_per_clip: 16
classes:
  wiggle:
    allow_flip: false
"""


def _workspace(sidecar=None, tau_in_yaml=None):
    d = tempfile.mkdtemp()
    body = CONFIG_YAML
    if tau_in_yaml is not None:
        body = body.replace("tau_high: null", "tau_high: %s" % tau_in_yaml)
    with open(os.path.join(d, "config.yaml"), "w", encoding="utf-8") as fh:
        fh.write(body)
    if sidecar is not None:
        path = os.path.join(d, CALIBRATION_PATH)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(sidecar, fh)
    return d


def _status(**kwargs):
    d = _workspace(**kwargs)
    return calibration_status(load_config(os.path.join(d, "config.yaml")))


def test_demo_preset_is_never_calibrated():
    """The exact sidecar this repo shipped with."""
    status = _status(sidecar={
        "tau_high": 1.5,
        "source": "demo-preset (uncalibrated, see spec 8)",
    })
    assert status["state"] == "uncalibrated"
    assert status["tau_high"] == 1.5
    assert status["detail"], "an uncalibrated threshold must explain itself"


def test_sidecar_source_survives_the_load():
    """The provenance string must reach the caller, not be replaced by a path."""
    status = _status(sidecar={"tau_high": 1.5, "source": "demo-preset (uncalibrated)"})
    assert status["source"] == "demo-preset (uncalibrated)"
    assert CALIBRATION_PATH not in str(status["source"])


def test_absent_threshold_is_uncalibrated():
    status = _status()
    assert status["state"] == "uncalibrated"
    assert status["tau_high"] is None


def test_swept_threshold_is_calibrated():
    status = _status(sidecar={
        "tau_high": 2.41,
        "source": "scripts/calibrate.py",
        "within_gates": True,
        "within_budget": True,
    })
    assert status["state"] == "calibrated"
    assert status["tau_high"] == pytest.approx(2.41)
    assert status["detail"] is None


@pytest.mark.parametrize("flag", ["within_gates", "within_budget"])
def test_a_failed_gate_downgrades_to_provisional(flag):
    """Swept, but short of the spec 8.1 data gates or the FA budget."""
    sidecar = {"tau_high": 2.0, "source": "scripts/calibrate.py",
               "within_gates": True, "within_budget": True}
    sidecar[flag] = False
    status = _status(sidecar=sidecar)
    assert status["state"] == "provisional"
    assert status["detail"]


def test_yaml_threshold_is_trusted():
    """An explicit tau_high in config.yaml wins and is taken as calibrated."""
    status = _status(tau_in_yaml="2.0")
    assert status["state"] == "calibrated"
    assert status["source"] == "config.yaml"


# -- the webapp must not persist a preset ------------------------------------

def test_resolve_tau_does_not_write_the_sidecar(monkeypatch):
    """resolve_tau returns the demo value; it must leave no trace on disk.

    This is the regression guard on the deleted ensure_calibration, whose write
    is what let an unmeasured number outlive its session.
    """
    import webapp.server as server

    d = _workspace()
    monkeypatch.setattr(server, "CONFIG_PATH", os.path.join(d, "config.yaml"))
    c = load_config(os.path.join(d, "config.yaml"))

    tau, status = server.resolve_tau(c)

    assert tau == server.DEMO_TAU_HIGH
    assert status["state"] == "uncalibrated"
    assert not os.path.exists(os.path.join(d, CALIBRATION_PATH)), \
        "the demo preset must live in memory only, never in the sidecar"


def test_server_never_calls_save_calibration():
    """No webapp code path may write a threshold. Only calibrate.py does."""
    import inspect

    import webapp.server as server

    source = inspect.getsource(server)
    assert "save_calibration" not in source
    assert "ensure_calibration" not in source
