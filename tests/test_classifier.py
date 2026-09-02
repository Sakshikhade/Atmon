import numpy as np
import pytest

from src.classifier import BehaviorClassifier


def test_heuristic_frequency_calculation():
    # Initialize classifier with 30 FPS
    classifier = BehaviorClassifier(window_size=60, fps=30.0)

    # Generate a synthetic signal: 4 Hz oscillation
    # 4 Hz at 30 FPS means 30/4 = 7.5 frames per cycle
    # Normalized distance: 0.5 + 0.2 * sin(2 * pi * 4 * t)
    fps = 30.0
    for i in range(120):  # 4 seconds of data
        t = i / fps
        val = 0.5 + 0.2 * np.sin(2 * np.pi * 4.0 * t)
        result = classifier.update(val)

    # After 120 frames, the frequency should be around 4.0 Hz
    # We use a slightly more lenient tolerance for heuristic peak counting
    assert result["frequency"] == pytest.approx(4.0, abs=0.5)


def test_stimming_trigger():
    classifier = BehaviorClassifier(
        window_size=60,
        fps=30.0,
        sustained_duration=1.0,  # 30 frames
    )

    # Fast rhythmic signal (4 Hz, high velocity)
    fps = 30.0
    for i in range(150):  # 5 seconds
        t = i / fps
        val = 0.5 + 0.3 * np.sin(2 * np.pi * 4.0 * t)
        result = classifier.update(val)

    # Should trigger stimming
    assert result["is_stimming"] is True
    assert result["frequency"] > 3.0


def test_non_stimming_behavior():
    classifier = BehaviorClassifier(window_size=60, fps=30.0)

    # Slow movement (1 Hz)
    fps = 30.0
    for i in range(150):
        t = i / fps
        val = 0.5 + 0.1 * np.sin(2 * np.pi * 1.0 * t)
        result = classifier.update(val)

    assert result["is_stimming"] is False


def test_heuristic_fps_update_preserves_history():
    classifier = BehaviorClassifier(window_size=10, fps=30.0)

    # Fill activation window
    for i in range(10):
        classifier.activation_window.append(1)

    assert len(classifier.activation_window) == 10
    assert classifier.activation_window.maxlen == int(classifier.activation_window_duration * 30)

    # Update FPS
    classifier.fps = 60.0

    assert len(classifier.activation_window) == 10
    assert classifier.activation_window.maxlen == int(classifier.activation_window_duration * 60)
