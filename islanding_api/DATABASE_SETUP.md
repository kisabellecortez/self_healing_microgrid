# AEGIS `islanding_api` - Database Setup & Reference

Full explanation of the Postgres/TimescaleDB layer added to `islanding_api`.
Keep this in the repo - it's the reference doc for anyone (including future
you) touching the database.

---

In `main.py`, you'd import like:

```python
from database import get_db
from models import Decision, GridState, GridStateLog, AnomalyScore, FeatureReading, BatteryStatus, LoadStatus
```

---

## Running it

```bash
cd islanding_api
cp .env.example .env
docker compose up -d
pip install "sqlalchemy>=2.0" asyncpg python-dotenv
```

`docker compose up -d` pulls `timescale/timescaledb:latest-pg16`, starts
Postgres on `localhost:5432`, and auto-runs `init-db/001_schema.sql` the
*first* time the container starts (Postgres images only execute
`docker-entrypoint-initdb.d` scripts against a fresh, empty data volume). If
you edit the schema file after that first run, it won't re-apply
automatically - either `docker compose down -v` (wipes data, re-runs init
scripts) or apply the change manually with `psql`.

Adminer, a browser-based DB viewer, comes up at `http://localhost:8080`.
System: PostgreSQL, server: `db`, username/password/database from `.env`.
It's just a convenience for poking at tables without `psql` - delete that
service block in `docker-compose.yml` if you don't want it running.

To confirm it's working:

```bash
docker exec -it aegis_postgres psql -U aegis -d aegis -c '\dt'
```

should list 6 tables.

---

## Why TimescaleDB instead of plain Postgres

FS-11 and NFS-1 both explicitly call for a **time-series database** with
streaming ingestion. TimescaleDB is a Postgres extension, not a different
database - `timescale/timescaledb:latest-pg16` is regular Postgres 16 with
the extension pre-installed, so there's no cost to using it (same SQL, same
drivers, same everything) and you get automatic time-based partitioning
("hypertables") for the sensor/decision streams, which is what actually
matters at any real data volume. If you ever want to cite this in the
report: "time-series data is stored in TimescaleDB hypertables partitioned
on ingestion time" is a true, specific claim that maps directly to FS-11.

---

## Schema, table by table

All tables live in `init-db/001_schema.sql`. Every one is a **hypertable**
partitioned on its `time` column via `SELECT create_hypertable(...)`.

### `feature_readings` - FS-6, FS-7, FS-8, FS-11, FS-12
The raw/computed electrical features streamed from the edge MCU. One row per
`(time, node_id)`.
- `voltage`, `current`, `frequency`, `fault_probability`, `soc` - the core
  measurements (FS-6, FS-7)
- `season`, `environment` (JSONB) - contextual info for anomaly detection
  (FS-12). JSONB rather than fixed columns because "environmental
  conditions" isn't pinned down in the spec yet - drop whatever you end up
  measuring (temp, humidity, etc.) into that field without a migration.

### `anomaly_scores` - FS-13, FS-14
Your teammate's layer. One row per anomaly detection run: `anomaly_score`
plus optional `node_id` (if scores are per-node) and `model_version` (so you
can tell which model version produced a given score once you're iterating).

### `grid_states` - FS-16, FS-17, FS-18
Output of grid state classification. `state` is one of the five values from
FS-17 (`normal`, `warning`, `critical`, `fault_imminent`, `islanded`),
enforced by a Postgres enum type so you can't accidentally insert a typo'd
state.

### `decisions` - FS-19, FS-20, FS-21, NFS-9
Your layer. `grid_state` records what state triggered the decision,
`action` is a free-text label for what was done (e.g. `"island"`,
`"shed_load_2"`, `"reconnect_load_1"`), `latency_ms` lets you check against
the NFS-9 100 ms target, and `outcome` is left nullable so you can backfill
it later once you know how the decision played out - that's what FS-20
("update future decisions based on outcomes of previous control actions")
needs to train against.

### `battery_status` - FS-4, FS-5
Tracks SOC and which battery is currently active, per the battery priority
logic in FS-4/FS-5.

### `load_status` - FS-9, FS-10, FS-22
Per-load connection state (`connected` bool) and `priority_level`, matching
the staggered reconnection timing in FS-10 and the independent per-load
switching in FS-22.

No `demand_forecast` table - per your call with your teammate, anomaly
detection is doing that job instead.

---

## Schema source of truth = the SQL file, not the ORM

Don't call `Base.metadata.create_all()` against this database. Hypertable
conversion (`SELECT create_hypertable(...)`) isn't something SQLAlchemy can
express, so `init-db/001_schema.sql` is what actually builds the schema
(via Docker's init-script mechanism). `models.py` is only for querying and
inserting against tables that already exist.

---

## Example: running a decision cycle from a route

```python
# main.py
import time
from fastapi import FastAPI, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from decision_layer import build_load_signals, determine_action, log_decision, time_since_islanding_started

app = FastAPI()

@app.post("/decide")
async def decide(system_anomaly_score: float, soc: float,
                  anomaly_scores: dict[str, float], connected: dict[str, bool], critical: dict[str, bool],
                  db: AsyncSession = Depends(get_db)):
    loads = build_load_signals(anomaly_scores, connected, critical)
    time_islanded_sec = await time_since_islanding_started(db)

    start = time.perf_counter()
    grid_state, branch, actions, features = determine_action(system_anomaly_score, loads, soc, time_islanded_sec)
    latency_ms = (time.perf_counter() - start) * 1000

    await log_decision(db, grid_state=grid_state, branch=branch, actions=actions,
                        features=features, latency_ms=latency_ms)
    return {"grid_state": grid_state, "actions": {k: v.value for k, v in actions.items()}}
```

`build_load_signals` is a plain sync function now, no DB access needed -
it just zips the three interface dicts into `LoadSignal` objects.
`time_since_islanding_started` is the one that hits the DB, resolving how
long the system has been islanded from `grid_states` history (see below).

---

## If the schema starts changing a lot

Worth adding Alembic for versioned migrations at that point instead of
hand-editing `001_schema.sql`. Left out for now to keep this minimal -
happy to set it up when it's actually needed.

---

## Applying migrations to an already-initialized DB

`init-db/*.sql` files only auto-run once, against an empty volume.
`002_decisions_features.sql` (adds `features` and `load_actions` to
`decisions`) needs to be applied by hand if your containers are already up:

```bash
docker exec -i aegis_postgres psql -U aegis -d aegis < init-db/002_decisions_features.sql
```

---

## Decision layer (`decision_layer.py`)

FS-18/19/20 plus the grid-classification duties (FS-15/16/17) it absorbed,
per 3.7.2 and confirmed with anomaly detection - there's no separate
classification module. Implements Figure Y from the doc function-for-
function; heavily commented in the file itself. This module was rebuilt,
not just relabeled, when FS-5/FS-9/FS-10 changed to the SOC-or-time staged
shedding model - critical loads no longer disconnect at islanding onset,
and shed individually instead of in bulk SOC tiers. Three things worth
knowing up front:

- `classify_grid_state()` auto-loads a trained model from
  `models/rf_grid_state.joblib` if one exists, otherwise falls back to
  rule-based thresholds. Nothing else in the file needs to change when a
  real model lands - just drop the file in place.
- Reconnection is unconditional now, not staggered - no spec defines a
  reconnect delay anymore (that concept moved to shedding, per the
  FS-10 rewrite). `reconnect_all()` just reconnects everything the moment
  grid state allows it.
- `log_decision()` writes to *both* `decisions` and `grid_states` now.
  The second one didn't used to get written to at all, which meant
  `time_since_islanding_started()` had no history to query. Don't remove
  that write, `time_since_islanding_started()` depends on it.
- The rest of the module (`classify_grid_state`, `map_state_to_action`,
  `determine_action`, `build_load_signals`) is pure and doesn't touch the
  DB. Only `time_since_islanding_started()` and `log_decision()` do.

---

## Training (`train_decision_model.py`)

Blocked on real Simulink data - runs against synthetic placeholder data for
now so the save/load/predict path is proven to work mechanically before
real data exists. Real usage once it lands:

```bash
python train_decision_model.py path/to/simulink_export.csv
```

Won't save/deploy a model that misses the NFS-7 recall target (90% on
`critical`/`fault_imminent`) unless you pass `--force` - intentional, not a
bug, given this model ends up deciding whether Critical Load 1 stays powered.