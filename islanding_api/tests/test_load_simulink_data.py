"""load_simulink_data.py's classify() is the single source of truth for
grid_state labels used both to backfill grid_states (so
retrain_isolation_forests.py's fault-window filter has real ground truth)
and to label the Random Forest training CSV (build_rf_training_csv.py) -
it must agree exactly with decision_layer.py's own rule-based thresholds.
"""

import pytest

from load_simulink_data import classify


@pytest.mark.parametrize("fault_frac,expected", [
    (0.0, "normal"),
    (0.1999, "normal"),
    (0.2, "warning"),
    (0.3999, "warning"),
    (0.4, "critical"),
    (0.5999, "critical"),
    (0.6, "fault_imminent"),
    (0.9999, "fault_imminent"),  # not islanded=True -> capped at fault_imminent even past 0.8
])
def test_classify_matches_decision_layer_thresholds_when_not_islanded(fault_frac, expected):
    assert classify(fault_frac, islanded=False) == expected


@pytest.mark.parametrize("fault_frac", [0.0, 0.3, 0.5, 0.7, 0.9])
def test_classify_islanded_flag_overrides_threshold(fault_frac):
    # Ground truth from the simulation wins over a threshold guess, even at
    # a fault_frac that would otherwise classify as normal/warning/etc.
    assert classify(fault_frac, islanded=True) == "islanded"
