"""Nightly retraining of the per-load Isolation Forest models (Section 3.7.1).
Invoked as a subprocess by main.py's "daily_retraining" cron job at 3am.

BLOCKED on a real historical-data source (see NEXT_STEPS.md): the design
doc's retraining description ("previous day's valid operating samples...
[when] the load state indicates that the load is connected and the fault
status is false") assumes a `historic_grid_data` table that was never added
to the schema - init-db/*.sql only defines feature_readings, anomaly_scores,
grid_states, decisions, battery_status, load_status, and load_metadata.
Reconstructing "connected + not faulted" history from what's actually there
(most likely feature_readings joined against load_status and grid_states) is
a real design decision, not something to guess at here.
fetch_training_samples() below raises clearly rather than silently querying
a table that doesn't exist, so the nightly job fails loud-and-caught (see
main.py's run_training_script) instead of crashing on an opaque
"relation does not exist" error.

Previously this module: (1) connected to Postgres with empty
host/database/user/password strings, which fails immediately; (2) used a
synchronous psycopg2 connection while the rest of the app standardized on
the async SQLAlchemy engine in database.py; (3) reassigned the module-level
name `load_data` to a list of query rows, shadowing the imported load_data
module, so load_data.load_metadata inside the old retrain_isolation_forests()
raised AttributeError on every call - the loop that was supposed to retrain
every load never got past the first iteration. Rewritten to reuse the shared
async engine and the load_metadata table (init-db/003_load_metadata.sql).
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


async def fetch_load_metadata(db) -> dict[int, dict]:
    result = await db.execute(text(
        "SELECT load_id, rated_voltage, rated_current FROM load_metadata"
    ))
    return {
        load_id: {"rated_voltage": rated_voltage, "rated_current": rated_current}
        for load_id, rated_voltage, rated_current in result.all()
    }


async def fetch_training_samples(db, load_id: int, start_day: datetime) -> list[tuple]:
    """(voltage, current, temperature, humidity, windspeed, rainfall) tuples
    for confirmed-normal samples of this load since start_day.

    Raises until the team decides how "connected + not faulted" historic
    samples get reconstructed from feature_readings/load_status/grid_states
    (or a materialized view built on top of them) - see module docstring.
    """
    raise NotImplementedError(
        f"No historic_grid_data source exists yet for load_id={load_id!r} "
        "(see NEXT_STEPS.md) - retraining cannot run until that's resolved."
    )


def train_one(load_id: int, samples: list[tuple], ratings: dict) -> IsolationForest:
    """Pure model-fitting step - takes already-fetched samples so it's
    testable without a database (see tests/test_retrain_isolation_forests.py)."""
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
        ratings_by_load = await fetch_load_metadata(db)
        for load_id, ratings in ratings_by_load.items():
            samples = await fetch_training_samples(db, load_id, start_day)
            train_one(load_id, samples, ratings)


if __name__ == "__main__":
    asyncio.run(retrain_all())
