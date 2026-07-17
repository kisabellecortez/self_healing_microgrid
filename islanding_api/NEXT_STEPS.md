# Integration status, testing, and next steps

Written while connecting the anomaly detection layer (3.7.1, teammate) to
the decision layer (3.7.2, Devrim) for the first time, then updated when
`node_data`/`historic_grid_data` were added. Covers what was broken, what
got fixed, the test suite, and what's still blocked on real training data.
Read this before touching `anomaly_detection.py`, `load_data.py`,
`retrain_isolation_forests.py`, or `main.py`'s `/api/islanding` route.

## Update: `node_data` + `historic_grid_data` added

The anomaly-detection side proposed two new tables (screenshot: `Table 1.
historic_grid_data`, `Table 2. node_data`). One design decision worth
flagging back to her:

- **`node_data` merged into the existing `load_metadata` table** rather than
  being added alongside it - her proposal (load_id, priority_level,
  voltage_rating, current_rating, power_rating) covered the same 5 rows
  `load_metadata` already did, just under different column names and
  missing the `name`/`load_type` columns 3.7.2 depends on. Two tables
  claiming to be the source of truth for the same ratings would drift out
  of sync eventually, so `init-db/004_node_data.sql` renames
  `load_metadata` to `node_data`, adopts her column names, and keeps `name`
  (decision_layer.py's string ids) and `load_type` (the critical flag)
  alongside them. `init-db/003_load_metadata.sql` is left in place but
  marked superseded, not deleted, in case it was already applied somewhere.
- **`historic_grid_data` added as proposed** (`init-db/005_historic_grid_data.sql`),
  with one naming change (`time` not `timestamp`, matching every other
  hypertable in this schema) and a FK to `node_data(load_id)`. This is the
  table `retrain_isolation_forests.py::fetch_training_samples()` was
  previously blocked on (see "Resolved" below) - it now has a real source.
  `main.py`'s `/api/islanding` writes one row per recognized load per
  request, alongside the `anomaly_scores` write it already did.
- **Open question for the anomaly-detection side**: her `historic_grid_data.state`
  column can only represent connected/disconnected (that's the only sense
  of "state" defined anywhere - the JSON payload's per-load `state` field).
  But Section 3.7.1's retraining description filters on "connected **and**
  fault status is false," and a load can be connected while actively
  faulting - that's the whole point of anomaly detection. A single `state`
  column can't carry both. `fetch_training_samples()` currently resolves
  this by additionally excluding samples recorded while `grid_states` was
  `critical`/`fault_imminent`/`islanded` at that moment (a `LEFT JOIN
  LATERAL` finding the most recent grid state at-or-before each sample's
  time - see the function's docstring for why a plain `NOT IN` isn't
  enough). **Worth a quick confirm that this matches her intent** before
  relying on it - it's a reasonable reading of the doc, not something she
  explicitly signed off on.
- This query (the `LATERAL` join) hasn't been run against real Postgres yet
  - Docker wasn't available in this environment. Run `smoke_test.py`-style
  verification against a live DB before trusting it: seed a few
  `historic_grid_data`/`grid_states` rows spanning a fault window and
  confirm `fetch_training_samples()` excludes the right ones.

## TL;DR

Before the first pass, the app could not start (`import main` raised
`NameError` inside `load_data.py`), and even if it could, nothing anywhere
called both ML layers together - `main.py`'s `/api/islanding` was a GET
stub returning a placeholder string. All of that's fixed and covered by 51
pytest tests (`tests/`, no database required). `smoke_test.py` (does need
Docker/Postgres) still passes for the parts it covers and hasn't been
changed. Since then, `node_data`/`historic_grid_data` were added per the
anomaly-detection side's proposed schema - see the "Update" section above.

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
    tables that didn't exist yet. All three fixed now that `node_data` and
    `historic_grid_data` exist (see "Update" at the top) -
    `fetch_training_samples()` is a real query, not a placeholder.
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
  `load_data`-shadowing bug; `fetch_training_samples()` now runs a real
  query against `historic_grid_data` (see "Update" at the top) instead of
  raising `NotImplementedError`.
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

`tests/` (pytest, run with `pytest` from `islanding_api/`) - 51 tests, no
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
- `test_load_data.py` - `node_data` row mapping, model-loading edge cases.
- `test_retrain_isolation_forests.py` - `train_one`'s model fitting, and
  that `fetch_node_data`/`fetch_training_samples` issue the right
  tables/bindparams/bad-state list against a fake DB. Can't verify the
  `LATERAL` join's actual behavior without real Postgres - see the "Update"
  section at the top.

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
- **`retrain_isolation_forests.py`** (per-load Isolation Forests, 3.7.1): no
  longer blocked on a missing table (see "Update" at the top), but still
  blocked on there being enough real `historic_grid_data` history to train
  on - `retrain_all()` currently just skips a load with zero
  confirmed-normal samples rather than erroring, so the nightly job is safe
  to run before real data accumulates, it just won't do anything yet.
- **Rated current placeholders.** `init-db/004_node_data.sql` seeds
  `current_rating` with order-of-magnitude placeholders for everything
  except Critical Load 2 (derived directly from the doc's stated 11W/12V
  solenoid rating). Replace the FIT0441 motor and LED module entries with
  real datasheet or bench-measured values before training on real data -
  the anomaly detection feature vector (`current_deviation`) depends on
  them directly, and now so does `historic_grid_data`'s precomputed copy.
- **SOC ingestion.** `main.py::get_current_soc()` reads the most recent
  active row from `battery_status`, defaulting to `1.0` if that table is
  empty - which it will be, since nothing currently writes to it and the
  Figure 6 JSON payload doesn't carry SOC at all. Battery telemetry needs
  its own ingestion path (likely a small addition to the embedded payload,
  or a separate endpoint) before FS-4/FS-5 SOC-driven behavior is real
  end to end rather than always assuming a full battery.
