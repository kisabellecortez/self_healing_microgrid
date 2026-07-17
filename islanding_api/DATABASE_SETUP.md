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

### `node_data` - Section 3.4, feeds both 3.7.1 and 3.7.2 (added in `004_node_data.sql`, supersedes `load_metadata`/`003_load_metadata.sql`)
Static per-load reference data, seeded with the 5 prototype loads. This
table is what actually connects the two ML layers: `anomaly_detection.py`
needs `voltage_rating`/`current_rating` per load to compute its feature
vector, and `decision_layer.py` needs a `load_type` (critical/non_critical)
flag plus a stable string name (`critical_1`, `noncritical_1`, ...) -
`load_id` here is the integer id the embedded system uses in its JSON
payload (Figure 6); `name` is the string id
`decision_layer.py`/`CRITICAL_SHED_TRIGGERS` expect. `power_rating` is
nameplate rated power, not the instantaneous computed `power` column on
`historic_grid_data` below - don't confuse the two. Originally proposed by
the anomaly-detection side as `node_data` independent of the `load_metadata`
table this repo already had for the same 5 rows; `004_node_data.sql` merges
them into one table rather than keeping two overlapping sources of truth -
see `NEXT_STEPS.md`.

### `historic_grid_data` - Section 3.7.1 retraining source (added in `005_historic_grid_data.sql`)
One row per load per `/api/islanding` request, written alongside
`anomaly_scores`. Purpose-built for `retrain_isolation_forests.py` -
flattened weather columns (not JSONB, unlike `feature_readings`) and
precomputed `power`/`voltage_deviation`/`current_deviation` so the
retraining query doesn't need to join `node_data` and recompute per row (it
still does, at train time, against *current* ratings rather than trusting
these - see the file's docstring). `state` is connected/disconnected only,
matching the JSON payload's per-load `state` field - **not** the grid fault
state. `load_id` has a FK to `node_data(load_id)`, which is why
`main.py`'s endpoint must skip logging for any load_id with no `node_data`
row, the same guard it already needed for the name mapping.

Distinct from `feature_readings`: that table is the generic FS-11 raw
ingestion log for any node (JSONB environment, includes grid-wide
`frequency`/`soc` that don't belong on a per-load training row);
`historic_grid_data` is specifically shaped for 3.7.1's retraining query.
Both get written on every `/api/islanding` call - not a duplicate source of
truth, since they serve different consumers with different query needs.

---

## Schema source of truth = the SQL file, not the ORM

Don't call `Base.metadata.create_all()` against this database. Hypertable
conversion (`SELECT create_hypertable(...)`) isn't something SQLAlchemy can
express, so `init-db/001_schema.sql` is what actually builds the schema
(via Docker's init-script mechanism). `models.py` is only for querying and
inserting against tables that already exist.

---

## The decision cycle route: `POST /api/islanding` in `main.py`

This used to be a hypothetical example route (`/decide`, shown below in
earlier revisions of this doc) - it's now the real thing, and it's also
the actual connection point between 3.7.1 and 3.7.2:

```python
@app.post("/api/islanding")
async def islanding(payload: SensorPayload, db: AsyncSession = Depends(get_db)):
    data = payload.model_dump()
    raw_system_score, scores_predictions = process_json(data)      # 3.7.1
    # ...persist AnomalyScore rows, map int load_id -> decision_layer
    # string names via load_metadata, normalize the score...
    loads = build_load_signals(anomaly_scores, connected, critical)
    grid_state, branch, actions, features = determine_action(       # 3.7.2
        system_anomaly_score, loads, soc, time_islanded_sec
    )
    await log_decision(db, grid_state=grid_state, branch=branch, actions=actions,
                        features=features, latency_ms=latency_ms)
    return {"grid_state": grid_state.value, "actions": {...}}
```

`build_load_signals` is a plain sync function, no DB access needed - it
just zips three interface dicts into `LoadSignal` objects.
`time_since_islanding_started` is the one that hits the DB, resolving how
long the system has been islanded from `grid_states` history (see below).
See `NEXT_STEPS.md` for the score-scale normalization this route does and
why it's a placeholder pending real training data, and for where SOC
currently comes from (`battery_status`, defaulted to 1.0 if empty - not
part of the Figure 6 JSON payload).

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