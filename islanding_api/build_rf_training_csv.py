"""Builds the Random Forest training CSV (train_decision_model.py's
expected column shape) from the real Simulink exports, scored through the
just-retrained per-load Isolation Forest models - the same models
main.py's /api/islanding uses in production, not a synthetic proxy.

Must run after load_simulink_data.py and retrain_isolation_forests.py -
the models being scored against need to exist and actually be trained on
real data, or this just reproduces synthetic-quality noise under a
"real data" label.

Reproduces main.py's exact scoring logic rather than approximating it:
system_anomaly_score is normalize(mean(raw scores of connected loads)),
NOT mean(normalize(raw scores)) - those differ once normalize_anomaly_score's
clipping kicks in, and only the former is what production actually computes.

Usage:
    python build_rf_training_csv.py
    python train_decision_model.py rf_training_data.csv
"""

import glob
import os

import joblib
import numpy as np
import pandas as pd

from load_simulink_data import DATA_DIR, LOADS, classify
from main import normalize_anomaly_score

MODEL_DIR = os.path.join(os.path.dirname(__file__), "isolation_forest_models")
OUTPUT = os.path.join(os.path.dirname(__file__), "rf_training_data.csv")

# Section 3.4 ratings (init-db/004_node_data.sql) - kept in sync manually,
# same values load_simulink_data.py reads from node_data at load time.
RATINGS = {
    1: {"voltage_rating": 12.0, "current_rating": 0.30},
    2: {"voltage_rating": 12.0, "current_rating": 0.917},
    3: {"voltage_rating": 12.0, "current_rating": 0.20},
    4: {"voltage_rating": 12.0, "current_rating": 0.30},
    5: {"voltage_rating": 12.0, "current_rating": 0.20},
}
LOAD_NAMES = {1: "critical_1", 2: "critical_2", 3: "critical_3", 4: "noncritical_1", 5: "noncritical_2"}
CRITICAL_FLAG = {1: 1, 2: 1, 3: 1, 4: 0, 5: 0}

# normalize_anomaly_score uses Python's builtin min/max, which raises on a
# numpy array instead of comparing elementwise - np.vectorize still calls
# the real function per element (exact same formula, no reimplementation
# that could drift from main.py), just makes it array-safe to call here.
_normalize = np.vectorize(normalize_anomaly_score)


def score_file(df: pd.DataFrame, models: dict) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)

    raw_scores = np.full((len(df), len(LOADS)), np.nan)

    for col_idx, (load_id, sw_col, v_col, i_col, p_col) in enumerate(LOADS):
        name = LOAD_NAMES[load_id]
        rating = RATINGS[load_id]
        connected = df[sw_col] == 1

        voltage = df[v_col].to_numpy()
        current = df[i_col].to_numpy()
        power = df[p_col].to_numpy()
        voltage_deviation = voltage - rating["voltage_rating"]
        current_deviation = current - rating["current_rating"]

        features = np.column_stack([
            power, voltage_deviation, current_deviation,
            df["Temperature"].to_numpy(), df["Humidity"].to_numpy(),
            df["WindSpeed"].to_numpy(), df["Rainfall"].to_numpy(),
        ])

        load_raw = np.full(len(df), np.nan)
        if connected.any():
            load_raw[connected.to_numpy()] = models[load_id].decision_function(features[connected.to_numpy()])
        raw_scores[:, col_idx] = load_raw

        # Matches main.py exactly: normalized score if connected/scored, else 0.0.
        # load_raw is NaN at disconnected positions, but np.where only reads
        # the normalized branch where connected is True, so those NaNs never
        # reach `out` - _normalize(NaN) would itself just produce NaN, not 0.
        out[f"{name}_anomaly_score"] = np.where(connected, _normalize(load_raw), 0.0)
        out[f"{name}_connected"] = connected.astype(int)
        out[f"{name}_critical"] = CRITICAL_FLAG[load_id]

    # Rows where every load is disconnected -> raw_scores is all-NaN for
    # that row -> np.nanmean gives NaN (with a "Mean of empty slice"
    # warning, expected). Must check for this BEFORE calling _normalize:
    # normalize_anomaly_score's min(1.0, max(0.0, ...)) uses Python's
    # builtin min/max, which compare NaN as neither greater nor smaller
    # than anything - max(0.0, nan) silently evaluates to 0.0, not NaN, so
    # a NaN raw score would otherwise come out looking like a *perfectly
    # normal* reading instead of "no signal" once normalized. (Not a
    # reachable bug in main.py itself - it always short-circuits on
    # `raw_system_score is None` before calling normalize_anomaly_score,
    # so this NaN-swallowing quirk never fires in production; it only
    # bit here because np.nanmean produces float NaN instead of None.)
    all_disconnected = np.isnan(raw_scores).all(axis=1)
    with np.errstate(invalid="ignore"):
        raw_system_score = np.nanmean(raw_scores, axis=1)
        out["system_anomaly_score"] = _normalize(raw_system_score)
    out.loc[all_disconnected, "system_anomaly_score"] = np.nan

    active_battery2 = df["BatterySwitch2"] == 1
    out["soc"] = np.where(active_battery2, df["SOC2"] / 100.0, df["SOC1"] / 100.0)

    fault_frac = df["FaultProbability"] / 100.0
    islanded = df["Islanded"] == 1
    out["grid_state"] = [classify(f, i) for f, i in zip(fault_frac, islanded)]

    # Rows where every load was disconnected have no system_anomaly_score
    # (matches main.py: process_json returns None, /api/islanding returns
    # "no connected loads reporting" and never calls determine_action at
    # all) - nothing for the classifier to learn from, so drop them rather
    # than train on a fabricated value.
    return out.dropna(subset=["system_anomaly_score"])


def main():
    models = {load_id: joblib.load(os.path.join(MODEL_DIR, f"load_{load_id}.joblib")) for load_id, *_ in LOADS}

    files = sorted(glob.glob(os.path.join(DATA_DIR, "*.csv")))
    if not files:
        print(f"No CSV files found in {DATA_DIR}")
        return

    chunks = []
    for i, path in enumerate(files):
        df = pd.read_csv(path)
        chunks.append(score_file(df, models))
        print(f"[{i + 1}/{len(files)}] {os.path.basename(path)}: {len(df)} rows scored")

    result = pd.concat(chunks, ignore_index=True)
    result.to_csv(OUTPUT, index=False)
    print(f"\nWrote {len(result)} rows to {OUTPUT}")
    print(result["grid_state"].value_counts())


if __name__ == "__main__":
    main()
