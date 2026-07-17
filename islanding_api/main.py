import subprocess
import sys
import time
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import load_data
from anomaly_detection import process_json
from database import AsyncSessionLocal, get_db
from decision_layer import build_load_signals, determine_action, log_decision, time_since_islanding_started
from models import AnomalyScore, HistoricGridData

app = FastAPI()

scheduler = BackgroundScheduler()

# Fixed: this used to subprocess "isolation_forest_models_manager.py", a
# filename that doesn't exist anywhere in the repo (the actual script is
# retrain_isolation_forests.py) - the nightly 3am retraining job has been
# silently failing every run since it was added.
TRAIN_SCRIPT = "retrain_isolation_forests.py"


def run_training_script():
    try:
        subprocess.run([sys.executable, TRAIN_SCRIPT], check=True)
        load_data.models = load_data.load_models()
    except subprocess.CalledProcessError as e:
        print("Training failed: ", e.stderr)


@app.on_event("startup")
async def startup_event():
    load_data.models = load_data.load_models()
    async with AsyncSessionLocal() as db:
        load_data.load_metadata = await load_data.load_params(db)

    scheduler.add_job(
        run_training_script,
        "cron",
        hour=3,
        minute=0,
        id="daily_retraining",
        replace_existing=True
    )

    scheduler.start()

@app.on_event("shutdown")
def shutdown_event():
    scheduler.shutdown()

@app.get("/")
def home():
    return {"message": "FastAPI is running."}

@app.get("/api/data")
def data():
    return {"message": "Database data insertion."}


# ── FS-13/14/15/17/18/19: the actual connection between 3.7.1 and 3.7.2 ────
#
# Previously this was a GET stub returning a placeholder message - nothing
# in the repo called process_json() (anomaly_detection.py) or
# determine_action()/log_decision() (decision_layer.py) together. This
# endpoint is the missing bridge: one embedded-system JSON payload in
# (Figure 6, Section 3.6) drives both layers and returns a switching action.


class WeatherPayload(BaseModel):
    temperature: float
    humidity: float
    rainfall: float
    windspeed: float


class LoadPayload(BaseModel):
    load_id: int
    voltage: float
    current: float
    power: float
    state: int  # 0 = disconnected, 1 = connected (Figure 6)


class SensorPayload(BaseModel):
    timestamp: datetime
    weather: WeatherPayload
    loads: list[LoadPayload]


def normalize_anomaly_score(raw_score: float) -> float:
    """Maps an IsolationForest decision_function value (roughly centered on
    0, negative = more anomalous, not formally bounded to a fixed range)
    onto the ~0-1 "badness" scale decision_layer.py's rule-based fallback
    thresholds assume (_STATE_THRESHOLDS: 0.2/0.4/0.6/0.8, higher = worse).

    This is a placeholder linear mapping, not a calibrated one.
    decision_layer.py's thresholds were written against the Simulink
    fault-probability scale (Section 3.5) which is bounded [0, 1] by
    construction; nothing has yet verified that raw isolation-forest scores
    on real electrical data land in comparable bins. Revisit once real
    training data lets you check the actual score distribution and
    recalibrate this mapping (or replace it with one fit to real data) -
    see NEXT_STEPS.md.
    """
    return min(1.0, max(0.0, 0.5 - raw_score))


async def get_current_soc(db: AsyncSession) -> float:
    """Most recent SOC from whichever battery is currently active (FS-4/5).

    Defaults to 1.0 (full charge) if battery_status has no rows yet. The
    Figure 6 JSON payload this endpoint receives doesn't carry SOC at all -
    battery telemetry needs its own ingestion path before this default
    matters in practice. See NEXT_STEPS.md.
    """
    result = await db.execute(text(
        "SELECT soc FROM battery_status WHERE active = true ORDER BY time DESC LIMIT 1"
    ))
    row = result.first()
    return row[0] if row is not None else 1.0


@app.post("/api/islanding")
async def islanding(payload: SensorPayload, db: AsyncSession = Depends(get_db)):
    data = payload.model_dump()
    raw_system_score, scores_predictions = process_json(data)

    # One pass over the payload's loads does three things per recognized
    # load_id: (1) logs it to historic_grid_data (3.7.1 retraining source,
    # init-db/005_historic_grid_data.sql) regardless of connection state,
    # (2) persists its anomaly score (FS-13/14) if it was connected/scored,
    # (3) builds the connected/critical/anomaly_scores maps decision_layer.py
    # needs. A single guard - skip load_ids with no node_data row - covers
    # all three, since node_data.load_id is a FK target for
    # historic_grid_data and the only source of the string name
    # decision_layer.py and AnomalyScore.node_id both key on.
    anomaly_scores: dict[str, float] = {}
    connected: dict[str, bool] = {}
    critical: dict[str, bool] = {}
    for load in payload.loads:
        meta = load_data.load_metadata.get(load.load_id)
        if meta is None:
            continue  # no node_data row for this id - can't log or map it
        name = meta["name"]
        per_load = scores_predictions.get(load.load_id)  # None if disconnected/unscored

        db.add(HistoricGridData(
            load_id=load.load_id,
            voltage=load.voltage,
            current=load.current,
            power=per_load["power"] if per_load else None,
            voltage_deviation=per_load["voltage_deviation"] if per_load else None,
            current_deviation=per_load["current_deviation"] if per_load else None,
            temperature=payload.weather.temperature,
            humidity=payload.weather.humidity,
            wind_speed=payload.weather.windspeed,
            rainfall=payload.weather.rainfall,
            state=bool(load.state),
        ))

        connected[name] = bool(load.state)
        critical[name] = meta["critical"]
        if per_load:
            db.add(AnomalyScore(node_id=name, anomaly_score=per_load["score"]))
            anomaly_scores[name] = normalize_anomaly_score(per_load["score"])
        else:
            # Disconnected loads are never scored (anomaly_detection.py
            # skips state == 0), so they get a neutral 0.0 rather than a
            # KeyError - build_load_signals requires every connected-dict
            # key to have a matching anomaly_scores entry.
            anomaly_scores[name] = 0.0

    if raw_system_score is not None:
        db.add(AnomalyScore(node_id=None, anomaly_score=raw_system_score))
    await db.commit()

    if raw_system_score is None:
        # Every load reported state == 0 (all disconnected) - nothing to
        # classify or act on this cycle.
        return {"grid_state": None, "actions": {}, "detail": "no connected loads reporting"}

    system_anomaly_score = normalize_anomaly_score(raw_system_score)
    loads = build_load_signals(anomaly_scores, connected, critical)
    soc = await get_current_soc(db)
    time_islanded_sec = await time_since_islanding_started(db)

    start = time.perf_counter()
    grid_state, branch, actions, features = determine_action(
        system_anomaly_score, loads, soc, time_islanded_sec
    )
    latency_ms = (time.perf_counter() - start) * 1000

    await log_decision(db, grid_state=grid_state, branch=branch, actions=actions,
                        features=features, latency_ms=latency_ms)

    return {
        "grid_state": grid_state.value,
        "branch": branch,
        "actions": {k: v.value for k, v in actions.items()},
        "system_anomaly_score": system_anomaly_score,
    }
