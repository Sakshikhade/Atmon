"""Tests for event-level metrics and threshold selection (spec 8)."""


import pytest

from src.evaluate import (  # noqa: E402
    average_precision,
    confusion_matrix,
    evaluate,
    match_detections,
    select_threshold,
)

def det(cls, start, end, score):
    return {"class": cls, "start": start, "end": end, "score": score}

def gt(cls, start, end):
    return {"class": cls, "start": start, "end": end}

def test_exact_overlap_is_a_true_positive():
    matches, fps, missed = match_detections([det("a", 0, 10, 0.9)], [gt("a", 0, 10)])
    assert len(matches) == 1 and not fps and not missed

def test_wrong_class_is_not_a_match():
    matches, fps, missed = match_detections([det("b", 0, 10, 0.9)], [gt("a", 0, 10)])
    assert not matches and len(fps) == 1 and len(missed) == 1

def test_below_tiou_threshold_is_a_false_positive():
    # tIoU = 2/18 -- well under 0.5.
    matches, fps, missed = match_detections([det("a", 8, 18, 0.9)], [gt("a", 0, 10)])
    assert not matches and len(fps) == 1 and len(missed) == 1

def test_each_ground_truth_matches_at_most_one_detection():
    """Two good detections on one event: the better matches, the other is an FP."""
    detections = [det("a", 0, 10, 0.9), det("a", 1, 11, 0.8)]
    matches, fps, missed = match_detections(detections, [gt("a", 0, 10)])
    assert len(matches) == 1
    assert matches[0][0]["score"] == 0.9, "the higher-scoring detection must claim the event"
    assert len(fps) == 1 and not missed

def test_precision_recall_and_false_alarm_rate():
    detections = [det("a", 0, 10, 0.9), det("a", 100, 110, 0.8), det("a", 200, 210, 0.7)]
    ground_truth = [gt("a", 0, 10), gt("a", 300, 310)]
    result = evaluate(detections, ground_truth, total_hours=2.0)

    assert (result["tp"], result["fp"], result["fn"]) == (1, 2, 1)
    assert result["precision"] == pytest.approx(1 / 3)
    assert result["recall"] == pytest.approx(0.5)
    assert result["false_alarms_per_hour"] == pytest.approx(1.0)

def test_perfect_detection_scores_one():
    ground_truth = [gt("a", 0, 10), gt("b", 50, 60)]
    detections = [det("a", 0, 10, 0.9), det("b", 50, 60, 0.9)]
    result = evaluate(detections, ground_truth, total_hours=1.0)
    assert result["precision"] == 1.0 and result["recall"] == 1.0
    assert result["map"][0.5] == pytest.approx(1.0)
    assert result["false_alarms_per_hour"] == 0.0

def test_average_precision_rewards_ranking():
    """A true positive ranked above the false positives beats the reverse."""
    ground_truth = [gt("a", 0, 10)]
    good = [det("a", 0, 10, 0.9), det("a", 100, 110, 0.1)]
    bad = [det("a", 0, 10, 0.1), det("a", 100, 110, 0.9)]
    assert average_precision(good, ground_truth, 0.5) > average_precision(bad, ground_truth, 0.5)

def test_no_detections_gives_zero_recall_not_a_crash():
    result = evaluate([], [gt("a", 0, 10)], total_hours=1.0)
    assert result["recall"] == 0.0 and result["precision"] == 0.0
    assert result["false_alarms_per_hour"] == 0.0

def test_confusion_matrix_records_class_swap():
    labels, matrix = confusion_matrix([det("b", 0, 10, 0.9)], [gt("a", 0, 10)], ["a", "b"])
    assert labels == ["a", "b", "(background)"]
    assert matrix[0][1] == 1, "ground truth 'a' predicted as 'b'"

def test_confusion_matrix_records_misses_and_spurious():
    detections = [det("a", 100, 110, 0.9)]
    ground_truth = [gt("a", 0, 10)]
    labels, matrix = confusion_matrix(detections, ground_truth, ["a", "b"])
    background = len(labels) - 1
    assert matrix[0][background] == 1, "the missed event"
    assert matrix[background][0] == 1, "the spurious detection"

def test_threshold_selection_respects_the_budget():
    rows = [
        {"tau_high": 0.5, "recall": 0.95, "f1": 0.6, "false_alarms_per_hour": 40.0},
        {"tau_high": 0.7, "recall": 0.80, "f1": 0.8, "false_alarms_per_hour": 4.0},
        {"tau_high": 0.9, "recall": 0.40, "f1": 0.5, "false_alarms_per_hour": 0.5},
    ]
    best, ok = select_threshold(rows, max_false_alarms_per_hour=5.0)
    assert ok and best["tau_high"] == 0.7, "highest recall inside the budget"

def test_threshold_selection_flags_an_unmeetable_budget():
    rows = [
        {"tau_high": 0.5, "recall": 0.9, "f1": 0.6, "false_alarms_per_hour": 40.0},
        {"tau_high": 0.9, "recall": 0.4, "f1": 0.5, "false_alarms_per_hour": 12.0},
    ]
    best, ok = select_threshold(rows, max_false_alarms_per_hour=5.0)
    assert not ok, "caller must be told no threshold met the budget"
    assert best["tau_high"] == 0.9
