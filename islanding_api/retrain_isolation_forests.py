"""Nightly retraining of the per-load Isolation Forest models (Section 3.7.1).
Invoked as a subprocess by main.py's "daily_retraining" cron job at 3am.

fetch_training_samples() now has a real historic-data source:
init-db/005_historic_grid_data.sql's historic_grid_data table (schema
proposed by the anomaly-detection side), written by main.py's
/api/islanding on every request. Section 3.7.1 defines "valid" training
samples as the load being connected AND the fault status being false.
historic_grid_data.state covers "connected" directly; "fault status false"
is resolved below by excluding any row recorded while the most recent
grid_states classification was critical/fault_imminent/islanded (the
LATERAL join in fetch_training_samples) - a load can be connected and
mid-fault at the same time, which is the entire point of anomaly detection,
so `state = true` alone isn't a strong enough filter on its own. Worth
confirming this interpretation against the anomaly-detection side's intent
for the `state` column.

Previously this module: (1) connected to Postgres with empty
host/database/user/password strings, which fails immediately; (2) used a
synchronous psycopg2 connection while the rest of the app standardized on
the async SQLAlchemy engine in database.py; (3) reassigned the module-level
name `load_data` to a list of query rows, shadowing the imported load_data
module, so load_data.load_metadata inside the old retrain_isolation_forests()
raised AttributeError on every call - the loop that was supposed to retrain
every load never got past the first iteration. Rewritten to reuse the shared
async engine and the node_data/historic_grid_data tables.
"""

import asyncio
import os
from datetime import datetime, timedelta

import joblib
from sklearn.ensemble import IsolationForest
from sqlalchemy import text

from database import AsyncSessionLocal

N_ESTIMATORS = 200
CONTAMINATION = 0.01
RANDOM_STATE = 42

MODEL_DIR = os.path.join(os.path.dirname(__file__), "isolation_forest_models")

# A sample recorded while the grid was in any of these states reflects a
# fault (or the system already reacting to one), not confirmed-normal
# operation - excluded from training regardless of that load's own
# connected/disconnected state. Hardcoded, not user input, so inlining
# these into the SQL string below (rather than a bound IN-list) is safe.
_BAD_GRID_STATES = ("critical", "fault_imminent", "islanded")


async def fetch_node_data(db) -> dict[int, dict]:
    result = await db.execute(text(
        "SELECT load_id, voltage_rating, current_rating FROM node_data"
    ))
    return {
        load_id: {"rated_voltage": voltage_rating, "rated_current": current_rating}
        for load_id, voltage_rating, current_rating in result.all()
    }


async def fetch_training_samples(db, load_id: int, start_day: datetime) -> list[tuple]:
    """(voltage, current, temperature, humidity, wind_speed, rainfall) tuples
    for confirmed-normal samples of this load since start_day: connected
    (historic_grid_data.state) and not recorded during a fault-related grid
    state (via the LATERAL join against grid_states below).

    The LATERAL join finds, per historic_grid_data row, the most recent
    grid_states classification at or before that row's time - a plain
    `grid_states.state NOT IN (...)` filter without it would just check
    whether a bad state EVER happened before that time, not whether the
    grid was IN one at that moment. LEFT JOIN (not CROSS JOIN) so a sample
    recorded before grid_states has any history yet isn't dropped just
    because there's nothing to compare it against.
    """
    bad_states_sql = ", ".join(f"'{s}'" for s in _BAD_GRID_STATES)
    result = await db.execute(text(f"""
        SELECT h.voltage, h.current, h.temperature, h.humidity, h.wind_speed, h.rainfall
        FROM historic_grid_data h
        LEFT JOIN LATERAL (
            SELECT state FROM grid_states g
            WHERE g.time <= h.time
            ORDER BY g.time DESC
            LIMIT 1
        ) AS current_grid_state ON true
        WHERE h.load_id = :load_id
          AND h.state = true
          AND h.time >= :start_day
          AND (current_grid_state.state IS NULL OR current_grid_state.state NOT IN ({bad_states_sql}))
    """), {"load_id": load_id, "start_day": start_day})
    return result.all()


def train_one(load_id: int, samples: list[tuple], ratings: dict) -> IsolationForest:
    """Pure model-fitting step - takes already-fetched samples so it's
    testable without a database (see tests/test_retrain_isolation_forests.py).

    Recomputes power/voltage_deviation/current_deviation from raw
    voltage/current against CURRENT node_data ratings, rather than trusting
    historic_grid_data's precomputed columns of the same name - so a later
    correction to node_data doesn't leave stale deviations baked into
    old training rows.
    """
    training_dataset = []
    for voltage, current, temperature, humidity, windspeed, rainfall in samples:
        power = voltage * current
        voltage_deviation = voltage - ratings["rated_voltage"]
        current_deviation = current - ratings["rated_current"]
        training_dataset.append([
            power, voltage_deviation, current_deviation, temperature, humidity, windspeed, rainfall,
        ])

    model = IsolationForest(n_estimators=N_ESTIMATORS, contamination=CONTAMINATION, random_state=RANDOM_STATE)
    model.fit(training_dataset)

    os.makedirs(MODEL_DIR, exist_ok=True)
    joblib.dump(model, os.path.join(MODEL_DIR, f"load_{load_id}.joblib"))
    return model


async def retrain_all() -> None:
    """Retrains all loads currently discovered in the previous day's
    12-month rolling window, per Section 3.7.1's retraining strategy."""
    prev_day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    start_day = prev_day - timedelta(days=365)

    async with AsyncSessionLocal() as db:
        ratings_by_load = await fetch_node_data(db)
        for load_id, ratings in ratings_by_load.items():
            samples = await fetch_training_samples(db, load_id, start_day)
            if not samples:
                print(f"load_id={load_id}: no confirmed-normal samples since {start_day} - skipping retrain")
                continue
            train_one(load_id, samples, ratings)


if __name__ == "__main__":
    asyncio.run(retrain_all())
