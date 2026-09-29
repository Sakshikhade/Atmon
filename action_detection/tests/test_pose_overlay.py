"""Geometry for the skeleton overlay, tested in Python.

There is no JS test runner in this repo, so every coordinate decision --
mirroring, visibility filtering, edge topology -- lives in src/pose.py where
pytest can reach it. The only untested JS is canvas plumbing.
"""

import numpy as np
import pytest

from src.pose import POSE_EDGES, UPPER_BODY, landmarks_to_overlay

K = len(UPPER_BODY)


def _xyz(overrides=None):
    """A plausible landmark array; every point at (0.5, 0.5) unless overridden."""
    xyz = np.full((K, 3), 0.5, dtype=np.float32)
    for idx, value in (overrides or {}).items():
        xyz[int(idx)] = value
    return xyz


# -- topology ----------------------------------------------------------------

def test_edges_are_well_formed():
    for a, b in POSE_EDGES:
        assert 0 <= a < K and 0 <= b < K, "edge (%d,%d) outside UPPER_BODY" % (a, b)
        assert a != b, "self-edge at %d" % a
    assert len(POSE_EDGES) == len({frozenset(e) for e in POSE_EDGES}), "duplicate edge"


def test_no_face_mesh_edges():
    """Indices 1-10 are eyes/ears/mouth. Joining them is a scribble, not data."""
    face = set(range(1, 11))
    for a, b in POSE_EDGES:
        assert not (a in face and b in face), "face-mesh edge (%d,%d)" % (a, b)


# -- mirroring ---------------------------------------------------------------

def test_mirror_flips_x_only():
    xyz = _xyz({11: (0.2, 0.8, 0.0)})
    points = landmarks_to_overlay(xyz, mirror=True)
    assert points[11] == [pytest.approx(0.8), pytest.approx(0.8)]


def test_no_mirror_is_identity():
    xyz = _xyz({11: (0.2, 0.8, 0.0)})
    points = landmarks_to_overlay(xyz, mirror=False)
    assert points[11] == [pytest.approx(0.2), pytest.approx(0.8)]


# -- filtering ---------------------------------------------------------------

def test_low_visibility_becomes_null():
    xyz = _xyz()
    vis = np.ones(K, dtype=np.float32)
    vis[11] = 0.1
    points = landmarks_to_overlay(xyz, vis, min_visibility=0.5)
    assert points[11] is None
    assert points[12] is not None


def test_nan_becomes_null():
    xyz = _xyz()
    xyz[13] = (np.nan, np.nan, np.nan)
    points = landmarks_to_overlay(xyz)
    assert points[13] is None


def test_all_nan_returns_none_not_a_list_of_nulls():
    """The client clears its canvas on a whole-payload None."""
    xyz = np.full((K, 3), np.nan, dtype=np.float32)
    assert landmarks_to_overlay(xyz) is None


def test_everything_filtered_out_returns_none():
    xyz = _xyz()
    vis = np.zeros(K, dtype=np.float32)
    assert landmarks_to_overlay(xyz, vis, min_visibility=0.5) is None


def test_out_of_range_is_preserved_not_clamped():
    """MediaPipe extrapolates occluded joints past the frame (crop.py saw 1.94).

    A canvas clips them naturally. Clamping would instead pin a flailing wrist
    to the frame edge and draw a bone that never existed.
    """
    xyz = _xyz({15: (-0.3, 1.94, 0.0)})
    points = landmarks_to_overlay(xyz, mirror=False)
    assert points[15] == [pytest.approx(-0.3), pytest.approx(1.94)]


def test_payload_is_json_shaped():
    """One entry per landmark, each [x, y] or null -- what the client indexes."""
    points = landmarks_to_overlay(_xyz())
    assert len(points) == K
    for p in points:
        assert p is None or (isinstance(p, list) and len(p) == 2)


# -- the landmarks_for_clip refactor -----------------------------------------

class _FakeDetector:
    """Stands in for MediaPipe; deterministic per-frame output."""

    def __init__(self):
        self.calls = 0

    def detect_frame(self, frame):
        self.calls += 1
        seed = int(frame[0, 0, 0])
        xyz = np.full((K, 3), seed / 255.0, dtype=np.float32)
        return xyz, np.ones(K, dtype=np.float32)


def test_landmarks_for_clip_equals_stacked_detect_frame():
    """The [T,K,3] contract must survive the per-frame refactor.

    The pose cache .npz and every DTW consumer depend on this exact shape, and
    IMAGE mode is stateless, so per-frame and per-clip results are identical.
    """
    from src.pose import PoseExtractor

    fake = _FakeDetector()
    clip = np.stack([np.full((4, 4, 3), i * 10, dtype=np.uint8) for i in range(6)])

    stacked = np.stack([fake.detect_frame(f)[0] for f in clip])
    fake.calls = 0
    got = PoseExtractor.landmarks_for_clip(fake, clip)

    assert got.shape == (len(clip), K, 3)
    assert got.dtype == np.float32
    np.testing.assert_array_equal(got, stacked)
    assert fake.calls == len(clip), "one detect per frame, no more"


def test_landmarks_for_clip_handles_an_empty_clip():
    from src.pose import PoseExtractor

    got = PoseExtractor.landmarks_for_clip(_FakeDetector(), np.zeros((0, 4, 4, 3), np.uint8))
    assert got.shape == (0, K, 3)
