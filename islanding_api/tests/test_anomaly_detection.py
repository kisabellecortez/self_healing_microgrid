"""Tests for anomaly_detection.py (Section 3.7.1, teammate's layer).

load_data.models / load_data.load_metadata are populated at FastAPI startup
in the real app; here they're monkeypatched directly so these tests don't
need a database or real trained Isolation Forest models.
"""

import load_data
from anomaly_detection import anomaly_detection, process_json, system_score_calc


class _FakeIsolationForest:
    """Records the feature vector it was called with, so tests can assert
    on exactly what anomaly_detection.py builds and passes to the model."""

    def __init__(self, score, prediction):
        self.score = score
        self.prediction = prediction
        self.last_features = None

    def decision_function(self, X):
        self.last_features = X[0]
        return [self.score]

    def predict(self, X):
        return [self.prediction]


def _install(monkeypatch, models: dict, metadata: dict):
    monkeypatch.setattr(load_data, "models", models)
    monkeypatch.setattr(load_data, "load_metadata", metadata)


def test_anomaly_detection_returns_model_score_and_prediction(monkeypatch):
    model = _FakeIsolationForest(score=0.42, prediction=1)
    _install(monkeypatch, {1: model}, {1: {"rated_voltage": 12.0, "rated_current": 0.3, "critical": True}})

    score, prediction = anomaly_detection(1, [1, 2, 3])
    assert score == 0.42
    assert prediction == 1


def test_process_json_builds_correct_feature_vector(monkeypatch):
    model = _FakeIsolationForest(score=0.1, prediction=1)
    _install(monkeypatch, {1: model}, {1: {"rated_voltage": 12.0, "rated_current": 0.3, "critical": True}})

    data = {
        "weather": {"temperature": 28.4, "humidity": 65, "rainfall": 0.0, "windspeed": 12.5},
        "loads": [{"load_id": 1, "voltage": 12.5, "current": 0.35, "power": 4.375, "state": 1}],
    }
    process_json(data)

    power, voltage_deviation, current_deviation, temperature, humidity, windspeed, rainfall = model.last_features
    assert power == 12.5 * 0.35
    assert voltage_deviation == 12.5 - 12.0
    assert abs(current_deviation - (0.35 - 0.3)) < 1e-9
    assert (temperature, humidity, windspeed, rainfall) == (28.4, 65, 12.5, 0.0)


def test_process_json_exposes_power_and_deviations_for_historic_logging(monkeypatch):
    # main.py's historic_grid_data writes (Section 3.7.1 retraining source)
    # need these alongside score/prediction - they were computed internally
    # already (for the model's feature_vector) but not returned before.
    model = _FakeIsolationForest(score=0.1, prediction=1)
    _install(monkeypatch, {1: model}, {1: {"rated_voltage": 12.0, "rated_current": 0.3, "critical": True}})

    data = {
        "weather": {"temperature": 20, "humidity": 50, "rainfall": 0, "windspeed": 5},
        "loads": [{"load_id": 1, "voltage": 12.5, "current": 0.35, "power": 4.375, "state": 1}],
    }
    _, scores_predictions = process_json(data)

    entry = scores_predictions[1]
    assert entry["power"] == 12.5 * 0.35
    assert entry["voltage_deviation"] == 12.5 - 12.0
    assert abs(entry["current_deviation"] - (0.35 - 0.3)) < 1e-9
    # Unchanged keys still present alongside the new ones.
    assert entry["score"] == 0.1
    assert entry["prediction"] == 1


def test_process_json_skips_disconnected_loads(monkeypatch):
    connected_model = _FakeIsolationForest(score=0.1, prediction=1)
    disconnected_model = _FakeIsolationForest(score=-0.9, prediction=-1)
    _install(
        monkeypatch,
        {1: connected_model, 2: disconnected_model},
        {
            1: {"rated_voltage": 12.0, "rated_current": 0.3, "critical": True},
            2: {"rated_voltage": 12.0, "rated_current": 0.3, "critical": False},
        },
    )

    data = {
        "weather": {"temperature": 20, "humidity": 50, "rainfall": 0, "windspeed": 5},
        "loads": [
            {"load_id": 1, "voltage": 12.0, "current": 0.3, "power": 3.6, "state": 1},
            {"load_id": 2, "voltage": 0.0, "current": 0.0, "power": 0.0, "state": 0},
        ],
    }
    system_score, scores_predictions = process_json(data)

    # Only the connected load should ever reach the model - a disconnected
    # load's zeroed voltage/current would otherwise look like a massive
    # deviation and get misread as a fault.
    assert set(scores_predictions) == {1}
    assert system_score == scores_predictions[1]["score"]


def test_process_json_returns_none_when_every_load_disconnected(monkeypatch):
    _install(monkeypatch, {1: _FakeIsolationForest(0.1, 1)}, {1: {"rated_voltage": 12.0, "rated_current": 0.3, "critical": True}})

    data = {
        "weather": {"temperature": 20, "humidity": 50, "rainfall": 0, "windspeed": 5},
        "loads": [{"load_id": 1, "voltage": 0.0, "current": 0.0, "power": 0.0, "state": 0}],
    }
    system_score, scores_predictions = process_json(data)

    # Previously this raised ZeroDivisionError inside system_score_calc.
    assert system_score is None
    assert scores_predictions == {}


def test_system_score_calc_averages_multiple_loads():
    scores_predictions = {1: {"score": 0.2, "prediction": 1}, 2: {"score": -0.4, "prediction": -1}}
    assert system_score_calc(scores_predictions) == (0.2 + -0.4) / 2


def test_system_score_calc_empty_returns_none():
    assert system_score_calc({}) is None


def test_process_json_skips_load_with_metadata_but_no_trained_model_yet(monkeypatch):
    # node_data has a row for load_id 1, but no isolation_forest_models/load_1.joblib
    # has ever been trained yet (real before the first successful retrain -
    # found via live testing: this previously KeyError'd inside
    # anomaly_detection() and 500'd the entire /api/islanding request).
    _install(monkeypatch, {}, {1: {"rated_voltage": 12.0, "rated_current": 0.3, "critical": True}})

    data = {
        "weather": {"temperature": 20, "humidity": 50, "rainfall": 0, "windspeed": 5},
        "loads": [{"load_id": 1, "voltage": 12.0, "current": 0.3, "power": 3.6, "state": 1}],
    }
    system_score, scores_predictions = process_json(data)

    assert system_score is None
    assert scores_predictions == {}


def test_process_json_skips_unknown_load_id_instead_of_keyerror(monkeypatch):
    # load_id 99 has no load_metadata entry - previously `ratings =
    # load_data.load_metadata[load_id]` raised KeyError here and crashed the
    # whole request over a single unrecognized/unseeded load id.
    _install(monkeypatch, {99: _FakeIsolationForest(0.1, 1)}, {})

    data = {
        "weather": {"temperature": 20, "humidity": 50, "rainfall": 0, "windspeed": 5},
        "loads": [{"load_id": 99, "voltage": 12.0, "current": 0.1, "power": 1.2, "state": 1}],
    }
    system_score, scores_predictions = process_json(data)

    assert system_score is None
    assert scores_predictions == {}
