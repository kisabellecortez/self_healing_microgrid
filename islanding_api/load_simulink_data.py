"""One-time bulk loader for real Simulink digital-twin exports (Section 3.5,
"Data Dumps/Data Dumps"/*.csv) into historic_grid_data and grid_states, so
the two ML training pipelines (train_decision_model.py,
retrain_isolation_forests.py) have real data to run on instead of synthetic
placeholders.

Two unit mismatches handled here, not upstream: the raw exports carry
FaultProbability and SOC1/SOC2 on a 0-100 scale, while every threshold in
decision_layer.py (_STATE_THRESHOLDS, CRITICAL_SHED_TRIGGERS) assumes 0-1 -
both divided by 100 on the way in. Missing this would silently break every
shedding decision trained/evaluated against this data.

Each file gets its own day-spaced base timestamp, all within the last 365
days (retrain_isolation_forests.py's rolling training window) - the raw
files otherwise all start at the identical in-file Timestamp value, which
would collide against historic_grid_data's (time, load_id) primary key if
inserted as-is.

grid_states rows are derived per timestep from FaultProbability (as a
system_anomaly_score stand-in, per decision_layer.py's own documented
convention - "the anomaly score already stands in for the fault probability
signal") run through the same _STATE_THRESHOLDS bins classify_grid_state
uses, with the raw data's own Islanded flag overriding to ISLANDED - ground
truth from the simulation beats a threshold heuristic. This grid_states
backfill is what lets retrain_isolation_forests.fetch_training_samples()'s
fault-window filter have real history to filter against, and doubles as the
label source for build_rf_training_csv.py.

Usage:
    python load_simulink_data.py                  # all files
    python load_simulink_data.py --files 1         # just the first file, for a dry run
"""

import argparse
import glob
import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import asyncio
from sqlalchemy import text

from database import AsyncSessionLocal

DATA_DIR = os.path.join(os.path.dirname(__file__), "Data Dumps", "Data Dumps")

LOADS = [
    (1, "SW1", "Crit1Voltage", "Crit1Current", "Crit1Power"),
    (2, "SW2", "Crit2Voltage", "Crit2Current", "Crit2Power"),
    (3, "SW3", "Crit3Voltage", "Crit3Current", "Crit3Power"),
    (4, "SW4", "NonCrit1Voltage", "NonCrit1Current", "NonCrit1Power"),
    (5, "SW5", "NonCrit2Voltage", "NonCrit2Current", "NonCrit2Power"),
]

BATCH_SIZE = 5000

HISTORIC_INSERT = text("""
    INSERT INTO historic_grid_data
        (time, load_id, voltage, current, power, voltage_deviation, current_deviation,
         temperature, humidity, wind_speed, rainfall, state)
    VALUES
        (:time, :load_id, :voltage, :current, :power, :voltage_deviation, :current_deviation,
         :temperature, :humidity, :wind_speed, :rainfall, :state)
""")

GRID_STATE_INSERT = text("""
    INSERT INTO grid_states (time, state, fault_probability, anomaly_score)
    VALUES (:time, CAST(:state AS grid_state_enum), :fault_probability, :anomaly_score)
""")


def classify(fault_probability_frac: float, islanded: bool) -> str:
    """Mirrors decision_layer._classify_with_thresholds/_STATE_THRESHOLDS
    exactly, so the derived label agrees with the deployed rule-based
    fallback. islanded=True (ground truth from the sim) always wins; a
    threshold crossing into the islanded bin without islanded actually
    being true gets capped at fault_imminent - the simulation's own
    islanding flag is more trustworthy than a threshold guess about
    hysteresis/delay in the physical model.

    Team-confirmed 2026-07-20: the raw Simulink exports only label
    `islanded` directly, not the other 4 states, so this threshold-based
    derivation is accepted as good enough rather than regenerating a
    dataset with real per-state ground truth - not worth the time on the
    team's schedule. Revisit if a real 5-way label ever becomes available
    (e.g. from Simulink's own Load Shedding Logic block) or if NFS-6
    numbers look off once real hardware data starts flowing.
    """
    if islanded:
        return "islanded"
    if fault_probability_frac < 0.2:
        return "normal"
    if fault_probability_frac < 0.4:
        return "warning"
    if fault_probability_frac < 0.6:
        return "critical"
    return "fault_imminent"


def load_file(path: str, base_time: datetime, ratings: dict) -> tuple[list[dict], list[dict]]:
    df = pd.read_csv(path)

    historic_rows = []
    grid_state_rows = []

    for _, row in df.iterrows():
        t = base_time + timedelta(seconds=float(row["Time"]))
        fault_frac = float(row["FaultProbability"]) / 100.0
        islanded = bool(row["Islanded"])

        grid_state_rows.append({
            "time": t,
            "state": classify(fault_frac, islanded),
            "fault_probability": fault_frac,
            "anomaly_score": fault_frac,
        })

        for load_id, sw_col, v_col, i_col, p_col in LOADS:
            connected = bool(row[sw_col])
            voltage = float(row[v_col])
            current = float(row[i_col])
            rating = ratings[load_id]
            historic_rows.append({
                "time": t,
                "load_id": load_id,
                "voltage": voltage,
                "current": current,
                "power": float(row[p_col]) if connected else None,
                "voltage_deviation": (voltage - rating["voltage_rating"]) if connected else None,
                "current_deviation": (current - rating["current_rating"]) if connected else None,
                "temperature": float(row["Temperature"]),
                "humidity": float(row["Humidity"]),
                "wind_speed": float(row["WindSpeed"]),
                "rainfall": float(row["Rainfall"]),
                "state": connected,
            })

    return historic_rows, grid_state_rows


async def insert_batches(db, stmt, rows: list[dict]):
    for i in range(0, len(rows), BATCH_SIZE):
        await db.execute(stmt, rows[i:i + BATCH_SIZE])
    await db.commit()


async def main(limit_files: int | None):
    files = sorted(glob.glob(os.path.join(DATA_DIR, "*.csv")))
    if limit_files:
        files = files[:limit_files]
    if not files:
        print(f"No CSV files found in {DATA_DIR}")
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(text("SELECT load_id, voltage_rating, current_rating FROM node_data"))
        ratings = {lid: {"voltage_rating": vr, "current_rating": cr} for lid, vr, cr in result.all()}

    # Spread files across the last ~300 days - comfortably inside
    # retrain_isolation_forests.py's 365-day rolling window, comfortably in
    # the past relative to "now" so nothing collides with live-tested rows.
    window_start = datetime.now(timezone.utc) - timedelta(days=300)

    total_historic = 0
    total_states = 0
    for i, path in enumerate(files):
        base_time = window_start + timedelta(days=i)
        historic_rows, grid_state_rows = load_file(path, base_time, ratings)

        async with AsyncSessionLocal() as db:
            await insert_batches(db, HISTORIC_INSERT, historic_rows)
            await insert_batches(db, GRID_STATE_INSERT, grid_state_rows)

        total_historic += len(historic_rows)
        total_states += len(grid_state_rows)
        print(f"[{i + 1}/{len(files)}] {os.path.basename(path)}: "
              f"{len(historic_rows)} historic_grid_data rows, {len(grid_state_rows)} grid_states rows "
              f"(base {base_time.date()})")

    print(f"\nDone: {total_historic} historic_grid_data rows, {total_states} grid_states rows "
          f"across {len(files)} files.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", type=int, default=None, help="Only load the first N files (for a dry run)")
    args = parser.parse_args()
    asyncio.run(main(args.files))
