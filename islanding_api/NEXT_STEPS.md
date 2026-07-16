# Integration status, testing, and next steps

Written while connecting the anomaly detection layer (3.7.1, teammate) to
the decision layer (3.7.2, Devrim) for the first time. Covers what was
broken, what got fixed, the new test suite, and what's still blocked on real
training data. Read this before touching `anomaly_detection.py`,
`load_data.py`, `retrain_isolation_forests.py`, or `main.py`'s
`/api/islanding` route.

## TL;DR

Before this pass, the app could not start (`import main` raised
`NameError` inside `load_data.py`), and even if it could, nothing anywhere
called both ML layers together - `main.py`'s `/api/islanding` was a GET
stub returning a placeholder string. All of that's fixed and covered by 46
new pytest tests (`tests/`, no database required). `smoke_test.py` (does
need Docker/Postgres) still passes for the parts it covers and hasn't been
changed.

## What was broken (found while wiring the two layers together)

1. **App couldn't start at all.** `load_data.py` called `conn.cursor()`
   with `conn` never defined - `NameError` at import time, which broke
   `import main` too since `main.py` imports `load_data`. Also depended on
   `psycopg2`, which wasn't in `requirements.txt`.
2. **Nothing connected the two layers.** `main.py`'s `/api/islanding` was a
   GET stub. Nothing in the repo called `anomaly_detection.process_json()`
   and `decision_layer.determine_action()`/`log_decision()` together.
3. **`load_id` type mismatch.** The embedded system's JSON payload (Figure
   6) identifies loads by small integers (1-5). `decision_layer.py` -
   including `CRITICAL_SHED_TRIGGERS`, which gates staged critical-load
   shedding - is keyed entirely on strings (`"critical_1"`,
   `"noncritical_1"`, ...). Nothing translated between the two. Left as-is,
   `CRITICAL_SHED_TRIGGERS.get(load.load_id)` would have silently returned
   `None` for every real load, meaning `_should_shed()` always returns
   `False` - **critical loads would never shed no matter how low the
   battery got.** Silent, not a crash - this was the riskiest bug found.
4. **No table backed either layer's per-load reference data.**
   `anomaly_detection.py` needs `rated_voltage`/`rated_current` per load;
   nothing in `init-db/*.sql` defined that. `load_data.py` queried a
   `load_metadata` table that didn't exist.
5. **`anomaly_scores` table was never written to**, despite `models.py`'s
   `AnomalyScore` and the table itself both already existing for exactly
   that purpose (FS-13/14).
6. **`system_score_calc` divided by zero** when every load in a payload was
   disconnected (`state == 0`) - an all-off reading crashed the request.
7. **`process_json` raised `KeyError`** on any `load_id` without a
   `load_metadata` row, instead of skipping it like it already does for
   disconnected loads.
8. **Score-scale mismatch.** `anomaly_detection.py`'s system score is an
   average of raw `IsolationForest.decision_function()` values - roughly
   centered on 0, negative = anomalous, not bounded to a fixed range.
   `decision_layer.py`'s rule-based fallback (`_STATE_THRESHOLDS`) assumes a
   ~0-1 scale matching the Simulink fault-probability signal. Feeding one
   directly into the other would have misclassified almost everything. See
   "Score normalization" below - this is flagged, not fully resolved.
9. **`main.py` subprocessed the wrong filename** for the nightly retraining
   job (`isolation_forest_models_manager.py`, which doesn't exist anywhere
   in the repo) - the 3am cron job has been silently failing every run
   since it was added.
10. **`retrain_isolation_forests.py`**: connected to Postgres with empty
    `host`/`database`/`user`/`password` strings (fails immediately);
    reassigned the module-level name `load_data` to a list of query rows,
    shadowing the imported `load_data` module, so
    `load_data.load_metadata[load_id]` raised `AttributeError` on every call
    inside the retrain loop; and queried `historic_grid_data`/`load_data`
    tables that don't exist. Fixed the first two; the third is a real open
    question, see below.
11. **Missing dependencies.** `apscheduler` (used by `main.py`) and
    `psycopg2` (used by the two files above) were imported but never in
    `requirements.txt`, so a clean `pip install -r requirements.txt` still
    wouldn't have run the app.

## What's fixed now

- `load_data.py` rewritten to use the app's existing async SQLAlchemy engine
  (`database.py`) instead of a broken standalone `psycopg2` connection.
- `init-db/003_load_metadata.sql` adds the `load_metadata` table (int
  `load_id` -> string `name`, `rated_voltage`, `rated_current`,
  `load_type`), seeded with the 5 prototype loads from Section 3.4. This is
  the table that resolves bug #3 - both layers now read the same mapping.
- `main.py`'s `POST /api/islanding` is now the real connection: JSON payload
  in -> `process_json()` -> persist `AnomalyScore` rows -> map int
  `load_id` to decision-layer names via `load_metadata` -> normalize the
  score -> `determine_action()` -> `log_decision()` -> per-load action out.
- `anomaly_detection.py`: `system_score_calc` returns `None` (not a crash)
  when nothing is connected; `process_json` skips unrecognized `load_id`s
  instead of raising.
- `retrain_isolation_forests.py`: fixed the connection and the
  `load_data`-shadowing bug; the historic-data query now raises a clear
  `NotImplementedError` naming the missing table rather than an opaque
  Postgres "relation does not exist" error.
- `requirements.txt` now lists everything actually imported
  (`apscheduler`, `pydantic`) plus the new test dependencies (`pytest`,
  `pytest-asyncio`, `httpx2`).

## Score normalization (flagged, not fully resolved)

`main.py::normalize_anomaly_score()` currently does `clip(0.5 - raw_score,
0, 1)` - a placeholder linear mapping from IsolationForest's
`decision_function` output onto the ~0-1 scale `decision_layer.py`'s
threshold fallback expects. This has not been validated against real (or
even synthetic-but-representative) IsolationForest output - it's a
reasonable guess at the right shape, not a calibrated transform. Once real
training data exists and the per-load models are trained on it, check the
actual `decision_function` distribution and either recalibrate the
constants or replace this with a proper transform (e.g. a fitted
sigmoid, or percentile-based scaling against the training set). This is
probably a 3.7.1/3.7.2 joint decision, not one layer's alone.

## Testing added

`tests/` (pytest, run with `pytest` from `islanding_api/`) - 46 tests, no
database required:

- `test_decision_layer.py` - every rule-based threshold boundary (all 5
  states), the trained-RF-vs-fallback switch, `build_feature_vector`,
  `build_load_signals`, all `map_state_to_action` branches, and the full
  SOC-or-time staged-shedding matrix for all three critical loads
  (extends the scenarios already in `smoke_test.py` with exact boundary
  values).
- `test_anomaly_detection.py` - feature-vector construction, disconnected-
  load skipping, the all-disconnected `None` case, the unknown-`load_id`
  skip case, and `system_score_calc` averaging.
- `test_main_integration.py` - the `/api/islanding` route end to end
  against a fake DB session, including a dedicated regression test for the
  int-`load_id`-to-string-name mapping (bug #3 above), anomaly-score
  persistence, the all-disconnected short-circuit, and an unrecognized
  `load_id` in the payload not crashing the request.
- `test_load_data.py`, `test_retrain_isolation_forests.py` - the pieces of
  each that don't depend on the still-missing historic-data source.

This suite is meant to run in CI/pre-commit on every change to either ML
layer or `main.py`'s `/api/islanding` route. `smoke_test.py` is unchanged
and still the one to run against a live Postgres instance
(`docker compose up -d`, then `python smoke_test.py`) before trusting the
logged-data path - it wasn't touched by this pass since it already covers
the decision-layer-to-Postgres path well.

## Still blocked on real training/historic data

- **`train_decision_model.py`** (grid-state Random Forest, 3.7.2): fully
  built and verified against synthetic data end to end, per its own
  docstring. Needs a Simulink export CSV with the columns listed at the top
  of that file. Run `python train_decision_model.py path\to\export.csv`
  once it exists; it won't save/deploy a model below the NFS-6 recall
  target without `--force`.
- **`retrain_isolation_forests.py`** (per-load Isolation Forests, 3.7.1):
  blocked on more than just data volume - `fetch_training_samples()` raises
  `NotImplementedError` because there's no schema-backed source for "the
  previous day's confirmed-normal samples" yet. The design doc's retraining
  description (Section 3.7.1: "load state indicates connected and fault
  status is false") implies joining `feature_readings` (has `voltage`,
  `current`, `environment` JSONB) against `load_status` (connected) and
  something indicating fault status - but there's no boolean fault column
  in the current schema, and `environment` is JSONB rather than flat
  temperature/humidity/windspeed/rainfall columns. **This needs a decision
  with the team, not a guess**: either add a `historic_grid_data` view/table
  that flattens what's needed out of the existing hypertables, or change
  what `feature_readings` stores. Whoever picks this up should start from
  `fetch_training_samples()`'s docstring in `retrain_isolation_forests.py`.
- **Rated current placeholders.** `init-db/003_load_metadata.sql` seeds
  `rated_current` with order-of-magnitude placeholders for everything except
  Critical Load 2 (derived directly from the doc's stated 11W/12V solenoid
  rating). Replace the FIT0441 motor and LED module entries with real
  datasheet or bench-measured values before training on real data - the
  anomaly detection feature vector (`current_deviation`) depends on them
  directly.
- **SOC ingestion.** `main.py::get_current_soc()` reads the most recent
  active row from `battery_status`, defaulting to `1.0` if that table is
  empty - which it will be, since nothing currently writes to it and the
  Figure 6 JSON payload doesn't carry SOC at all. Battery telemetry needs
  its own ingestion path (likely a small addition to the embedded payload,
  or a separate endpoint) before FS-4/FS-5 SOC-driven behavior is real
  end to end rather than always assuming a full battery.
