"""Live replay demo for showing the pipeline to a consultant: streams a
real Simulink scenario through the actual running /api/islanding endpoint,
in real (or sped-up) time, so the audience watches OUR real trained code
make real decisions on real recorded grid conditions - not a canned
example, and not Simulink doing the work.

IMPORTANT distinction this script exists to keep honest: Simulink is the
physics simulator plus its OWN independent reference control logic - the
SW1-5/Islanded/BatterySwitch columns in the CSV are what SIMULINK decided
while generating the data. Our code (anomaly_detection.py + decision_layer.py,
running live via /api/islanding, using models trained on this same CSV data)
is a completely separate Python implementation that independently decides
what to do when fed the same electrical readings. This script feeds our
system the readings a real embedded controller would send and prints our
system's live decision next to what Simulink itself did, as a sanity check
- it is not Simulink calling our code or vice versa. Say this out loud
during the demo; don't let "the simulation" sound like it means our code
ran inside Simulink.

Also writes real per-row SOC into battery_status before each request, so
get_current_soc() picks it up through its actual, unmodified production
code path rather than a demo-only bypass.

Usage:
    python demo_replay.py                                    # default scenario, 5x speed, whole file
    python demo_replay.py --rows 400                          # shorter highlight run
    python demo_replay.py "Data Dumps/Data Dumps/MicrogridTrainingData1.csv" --speed 10
"""

import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone

import pandas as pd
import requests
from sqlalchemy import text

from database import AsyncSessionLocal
from load_simulink_data import DATA_DIR, LOADS

API_URL = "http://127.0.0.1:8000/api/islanding"
DEFAULT_FILE = os.path.join(DATA_DIR, "MicrogridTrainingData (earlier islanding).csv")
LOG_PATH = os.path.join(os.path.dirname(__file__), "demo_replay_log.json")


async def write_battery_status(db, soc1: float, soc2: float, battery1_active: bool, battery2_active: bool):
    """Mirrors this row's real simulated battery state into battery_status,
    the same table main.py::get_current_soc() reads from in production -
    demo runs through the real query path, not a shortcut."""
    now = datetime.now(timezone.utc)
    await db.execute(text(
        "INSERT INTO battery_status (time, battery_id, soc, active) VALUES (:t, 'battery_1', :soc, :a)"
    ), {"t": now, "soc": soc1 / 100.0, "a": bool(battery1_active)})
    await db.execute(text(
        "INSERT INTO battery_status (time, battery_id, soc, active) VALUES (:t, 'battery_2', :soc, :a)"
    ), {"t": now, "soc": soc2 / 100.0, "a": bool(battery2_active)})
    await db.commit()


def build_payload(row: pd.Series) -> dict:
    """Same shape a real embedded controller would POST (Figure 6, Section 3.6)."""
    return {
        "timestamp": row["Timestamp"],
        "weather": {
            "temperature": float(row["Temperature"]),
            "humidity": float(row["Humidity"]),
            "rainfall": float(row["Rainfall"]),
            "windspeed": float(row["WindSpeed"]),
        },
        "loads": [
            {
                "load_id": load_id,
                "voltage": float(row[v_col]),
                "current": float(row[i_col]),
                "power": float(row[p_col]),
                "state": int(row[sw_col]),
            }
            for load_id, sw_col, v_col, i_col, p_col in LOADS
        ],
    }


async def run(path: str, speed: float, max_rows: int | None, quiet: bool):
    df = pd.read_csv(path)
    if max_rows:
        df = df.iloc[:max_rows]

    print(f"Replaying {os.path.basename(path)} ({len(df)} timesteps @ {speed}x speed)")
    print("Our system = live /api/islanding, real trained models. "
          "Simulink reference = what the digital twin itself decided while generating this data.\n")
    if not quiet:
        print(f"{'t(s)':>7} | {'fault':>5} | {'our state':<14} {'our shedding':<30} | simulink")
        print("-" * 100)

    log = []
    async with AsyncSessionLocal() as db:
        for _, row in df.iterrows():
            await write_battery_status(db, row["SOC1"], row["SOC2"], row["BatterySwitch1"], row["BatterySwitch2"])

            payload = build_payload(row)
            start = time.perf_counter()
            resp = requests.post(API_URL, json=payload, timeout=5)
            elapsed_ms = (time.perf_counter() - start) * 1000
            result = resp.json()

            sw_names = ["critical_1", "critical_2", "critical_3", "noncritical_1", "noncritical_2"]
            sim_shed = [name for name, sw in zip(sw_names, [row["SW1"], row["SW2"], row["SW3"], row["SW4"], row["SW5"]]) if sw == 0]
            sim_islanded = bool(row["Islanded"])

            our_state = result.get("grid_state") or "n/a"
            our_actions = result.get("actions", {})
            our_shed = [name for name, action in our_actions.items() if action == "shed"]

            log.append({
                "time_s": float(row["Time"]),
                "fault_probability_pct": float(row["FaultProbability"]),
                "our_grid_state": our_state,
                "our_shed": our_shed,
                "sim_islanded": sim_islanded,
                "sim_shed": sim_shed,
                "request_latency_ms": elapsed_ms,
            })

            if not quiet:
                sim_label = "ISLANDED" if sim_islanded else "grid-tied"
                shed_str = ",".join(our_shed) if our_shed else "-"
                print(f"{row['Time']:>7.1f} | {row['FaultProbability']:>4.0f}% | {our_state:<14} {shed_str:<30} | {sim_label}")

            await asyncio.sleep(0.1 / speed)

    with open(LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)

    islanded_steps = sum(1 for e in log if e["sim_islanded"])
    our_islanded_steps = sum(1 for e in log if e["our_grid_state"] == "islanded")
    agree = sum(1 for e in log if (e["our_grid_state"] == "islanded") == e["sim_islanded"])
    print(f"\nDone. {len(log)} steps logged to {LOG_PATH}")
    print(f"Simulink was islanded for {islanded_steps}/{len(log)} steps; "
          f"our system classified 'islanded' for {our_islanded_steps}/{len(log)} steps; "
          f"agreed on islanded-or-not for {agree}/{len(log)} steps ({100 * agree / len(log):.1f}%).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("file", nargs="?", default=DEFAULT_FILE)
    parser.add_argument("--speed", type=float, default=5.0, help="playback speed multiplier (5 = 5x real time)")
    parser.add_argument("--rows", type=int, default=None, help="limit to first N rows for a shorter run")
    parser.add_argument("--quiet", action="store_true", help="suppress per-row console output, just log")
    args = parser.parse_args()

    try:
        requests.get("http://127.0.0.1:8000/", timeout=2)
    except Exception:
        raise SystemExit(
            "API server not reachable at http://127.0.0.1:8000 - start it first:\n"
            "  uvicorn main:app --reload"
        )

    asyncio.run(run(args.file, args.speed, args.rows, args.quiet))
