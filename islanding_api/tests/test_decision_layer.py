"""Pure-logic tests for decision_layer.py (Section 3.7.2) - no database or
trained model required, so these run in CI/pre-commit with nothing but the
Python dependencies installed. Complements smoke_test.py, which additionally
exercises the Postgres logging path against a live database.
"""

import joblib
import pytest

import decision_layer
from decision_layer import (
    Action,
    GridState,
    LoadSignal,
    build_feature_vector,
    build_load_signals,
    classify_grid_state,
    determine_action,
    map_state_to_action,
)


def make_loads(connected=True):
    return build_load_signals(
        anomaly_scores={f"critical_{i}": 0.2 for i in (1, 2, 3)} | {f"noncritical_{i}": 0.2 for i in (1, 2)},
        connected={f"critical_{i}": connected for i in (1, 2, 3)} | {f"noncritical_{i}": connected for i in (1, 2)},
        critical={f"critical_{i}": True for i in (1, 2, 3)} | {f"noncritical_{i}": False for i in (1, 2)},
    )


# ── classify_grid_state: rule-based fallback thresholds ─────────────────
# _STATE_THRESHOLDS = [(0.2, NORMAL), (0.4, WARNING), (0.6, CRITICAL), (0.8, FAULT_IMMINENT)],
# upper bound exclusive; score >= 0.8 -> ISLANDED.

@pytest.mark.parametrize("score,expected", [
    (0.0, GridState.NORMAL),
    (0.1999, GridState.NORMAL),
    (0.2, GridState.WARNING),        # exactly on the boundary -> next bin, not NORMAL
    (0.3999, GridState.WARNING),
    (0.4, GridState.CRITICAL),
    (0.5999, GridState.CRITICAL),
    (0.6, GridState.FAULT_IMMINENT),
    (0.7999, GridState.FAULT_IMMINENT),
    (0.8, GridState.ISLANDED),
    (1.0, GridState.ISLANDED),
])
def test_classify_grid_state_thresholds(score, expected, no_trained_model):
    state = classify_grid_state({"system_anomaly_score": score})
    assert state == expected


class _FakeRFModel:
    """Picklable stand-in for a trained RandomForestClassifier - joblib needs
    a module-level class to serialize/deserialize it."""

    def __init__(self, state_value):
        self.state_value = state_value

    def predict(self, X):
        return [self.state_value] * len(X)


def test_classify_grid_state_prefers_trained_model_when_present(monkeypatch, tmp_path):
    model_path = tmp_path / "rf_grid_state.joblib"
    joblib.dump({"model": _FakeRFModel("fault_imminent"), "feature_names": ["system_anomaly_score", "soc"]}, model_path)

    monkeypatch.setattr(decision_layer, "MODEL_PATH", str(model_path))
    monkeypatch.setattr(decision_layer, "_rf_bundle", None)
    monkeypatch.setattr(decision_layer, "_rf_load_attempted", False)

    # Score of 0.01 would be NORMAL under the rule-based fallback - proves
    # the trained model path is actually taken, not silently ignored.
    state = classify_grid_state({"system_anomaly_score": 0.01, "soc": 1.0})
    assert state == GridState.FAULT_IMMINENT


# ── build_feature_vector / build_load_signals ────────────────────────────

def test_build_feature_vector_excludes_raw_voltage_current():
    loads = [LoadSignal("critical_1", anomaly_score=0.3, connected=True, critical=True)]
    features = build_feature_vector(system_anomaly_score=0.5, loads=loads, soc=0.8)
    assert features == {
        "system_anomaly_score": 0.5,
        "soc": 0.8,
        "critical_1_anomaly_score": 0.3,
        "critical_1_connected": 1,
        "critical_1_critical": 1,
    }


def test_build_load_signals_zips_by_load_id():
    loads = build_load_signals(
        anomaly_scores={"critical_1": 0.1, "noncritical_1": 0.2},
        connected={"critical_1": True, "noncritical_1": False},
        critical={"critical_1": True, "noncritical_1": False},
    )
    by_id = {l.load_id: l for l in loads}
    assert by_id["critical_1"] == LoadSignal("critical_1", 0.1, True, True)
    assert by_id["noncritical_1"] == LoadSignal("noncritical_1", 0.2, False, False)


# ── map_state_to_action: NORMAL/WARNING/CRITICAL/FAULT_IMMINENT branches ──

@pytest.mark.parametrize("state", [GridState.NORMAL, GridState.WARNING])
def test_normal_and_warning_reconnect_everything(state):
    loads = make_loads(connected=False)
    branch, actions = map_state_to_action(state, soc=0.9, loads=loads)
    assert branch == "reconnect_all"
    assert all(a == Action.RECONNECT for a in actions.values())


@pytest.mark.parametrize("state", [GridState.CRITICAL, GridState.FAULT_IMMINENT])
def test_critical_and_fault_imminent_shed_only_non_critical(state):
    loads = make_loads(connected=True)
    branch, actions = map_state_to_action(state, soc=0.9, loads=loads)
    assert branch == "shed_non_critical"
    assert actions["critical_1"] == Action.HOLD
    assert actions["critical_2"] == Action.HOLD
    assert actions["critical_3"] == Action.HOLD
    assert actions["noncritical_1"] == Action.SHED
    assert actions["noncritical_2"] == Action.SHED


# ── FS-5/FS-10: staged critical-load shedding, SOC-or-time triggers ──────

def test_islanded_state_never_sheds_critical_loads_at_healthy_soc_and_t0():
    loads = make_loads(connected=True)
    branch, actions = map_state_to_action(GridState.ISLANDED, soc=0.9, loads=loads, time_islanded_sec=0)
    assert branch == "staged_critical_shedding"
    for i in (1, 2, 3):
        assert actions[f"critical_{i}"] == Action.HOLD
    for i in (1, 2):
        assert actions[f"noncritical_{i}"] == Action.SHED


def test_critical_3_sheds_on_soc_below_20_percent_even_at_t0():
    loads = make_loads(connected=True)
    _, actions = map_state_to_action(GridState.ISLANDED, soc=0.15, loads=loads, time_islanded_sec=0)
    assert actions["critical_3"] == Action.SHED
    assert actions["critical_2"] == Action.HOLD
    assert actions["critical_1"] == Action.HOLD


def test_critical_2_sheds_after_59_minutes_islanded_at_healthy_soc():
    loads = make_loads(connected=True)
    _, held = map_state_to_action(GridState.ISLANDED, soc=0.9, loads=loads, time_islanded_sec=58 * 60)
    _, shed = map_state_to_action(GridState.ISLANDED, soc=0.9, loads=loads, time_islanded_sec=60 * 60)
    assert held["critical_2"] == Action.HOLD
    assert shed["critical_2"] == Action.SHED


def test_critical_1_is_soc_only_no_time_trigger():
    loads = make_loads(connected=True)
    # Holds at 11% no matter how long islanded...
    _, held = map_state_to_action(GridState.ISLANDED, soc=0.11, loads=loads, time_islanded_sec=10 ** 9)
    # ...but sheds immediately below 10%, regardless of elapsed time.
    _, shed = map_state_to_action(GridState.ISLANDED, soc=0.09, loads=loads, time_islanded_sec=0)
    assert held["critical_1"] == Action.HOLD
    assert shed["critical_1"] == Action.SHED


def test_shedding_is_monotonic_soc_recovering_does_not_unshed_within_same_call():
    """Not a stateful test (map_state_to_action is pure/stateless per call) -
    documents the invariant from decision_layer.py's comments: once a load's
    own trigger condition is met, this function alone will keep recommending
    SHED for any soc/time at or past that trigger. Reconnection only happens
    via a state change back to NORMAL/WARNING (see test above)."""
    loads = make_loads(connected=False)  # already shed
    _, actions = map_state_to_action(GridState.ISLANDED, soc=0.05, loads=loads, time_islanded_sec=0)
    assert actions["critical_1"] == Action.HOLD  # stays shed (disconnected + should stay disconnected)


# ── determine_action: end-to-end pure pipeline, all five states ─────────

@pytest.mark.parametrize("score,expected_state", [
    (0.05, GridState.NORMAL),
    (0.30, GridState.WARNING),
    (0.50, GridState.CRITICAL),
    (0.70, GridState.FAULT_IMMINENT),
    (0.90, GridState.ISLANDED),
])
def test_determine_action_covers_all_five_grid_states(score, expected_state, no_trained_model):
    loads = make_loads(connected=True)
    grid_state, branch, actions, features = determine_action(score, loads, soc=0.9, time_islanded_sec=0.0)
    assert grid_state == expected_state
    assert features["system_anomaly_score"] == score
    assert set(actions) == {"critical_1", "critical_2", "critical_3", "noncritical_1", "noncritical_2"}
