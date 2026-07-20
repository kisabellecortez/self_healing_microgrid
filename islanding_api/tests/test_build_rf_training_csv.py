"""Regression test for a real bug found while processing real Simulink data:
Python's builtin min()/max() (used inside main.normalize_anomaly_score)
silently treat NaN as neither greater nor smaller than anything -
max(0.0, float('nan')) evaluates to 0.0, not NaN. A row where every load is
disconnected produces a NaN raw_system_score (np.nanmean of an all-NaN
row); without the explicit all_disconnected check in score_file(), that
NaN would silently become system_anomaly_score = 0.0 - indistinguishable
from a perfectly healthy reading - instead of being dropped as "no signal."
"""

import numpy as np
import pandas as pd

from build_rf_training_csv import LOADS, score_file


class _FakeModel:
    def decision_function(self, X):
        return np.zeros(len(X))


def _make_row(all_disconnected: bool) -> dict:
    sw_value = 0 if all_disconnected else 1
    row = {
        "Temperature": 22.0, "Humidity": 50.0, "WindSpeed": 10.0, "Rainfall": 0.0,
        "FaultProbability": 15.0, "SOC1": 90.0, "SOC2": 100.0, "BatterySwitch2": 0,
        "Islanded": 0,
    }
    for load_id, sw_col, v_col, i_col, p_col in LOADS:
        row[sw_col] = sw_value
        row[v_col] = 12.0
        row[i_col] = 0.3
        row[p_col] = 3.6
    return row


def test_all_disconnected_row_is_dropped_not_scored_as_zero():
    df = pd.DataFrame([_make_row(all_disconnected=True), _make_row(all_disconnected=False)])
    models = {load_id: _FakeModel() for load_id, *_ in LOADS}

    result = score_file(df, models)

    # Only the connected row should survive - the all-disconnected row must
    # not appear at all, and definitely not as system_anomaly_score == 0.0
    # (which would misleadingly look like the healthiest possible reading).
    assert len(result) == 1
    assert not result["system_anomaly_score"].isna().any()
    assert (result["critical_1_connected"] == 1).all()
