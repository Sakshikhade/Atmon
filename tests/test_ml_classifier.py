from src.ml_classifier import MLBehaviorClassifier


def test_ml_fallback_to_heuristic():
    # Initialize with non-existent model path to force fallback
    classifier = MLBehaviorClassifier(model_path="non_existent.pkl")

    assert classifier.model is None

    # Update should still work using fallback
    result = classifier.update(0.5)
    assert "is_stimming" in result
    assert "frequency" in result


def test_ml_output_schema():
    classifier = MLBehaviorClassifier(model_path="non_existent.pkl")
    result = classifier.update(0.5)

    expected_keys = [
        "normalized_dist",
        "velocity",
        "mean_velocity",
        "std_dist",
        "frequency",
        "is_stimming",
        "elapsed_sustained",
        "consecutive_frames",
        "activation_ratio",
    ]

    for key in expected_keys:
        assert key in result


def test_ml_fps_update_preserves_history():
    classifier = MLBehaviorClassifier(window_size=10, fps=30.0, model_path="non_existent.pkl")
    # Force ML path to test MLBehaviorClassifier's own deques
    classifier.model = object()

    # Fill deques
    for i in range(5):
        classifier.update(0.1 * i)

    assert len(classifier.normalized_dists) == 5
    assert len(classifier.activation_window) == 5

    # Update FPS
    classifier.fps = 60.0

    # Feature deques MUST NOT be cleared
    assert len(classifier.normalized_dists) == 5
    assert classifier.normalized_dists.maxlen == 10

    # Activation window should adopt the new maxlen (duration * 60) but PRESERVE history
    assert len(classifier.activation_window) == 5
    assert classifier.activation_window.maxlen == int(classifier.activation_window_duration * 60)
