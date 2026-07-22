# Integration status, testing, and next steps

Living doc covering the state of the ML pipeline connecting 3.7.1 (anomaly
detection) and 3.7.2 (decision layer). Previously covered only integration
bug fixes and synthetic-data testing; as of 2026-07-20 both ML layers are
trained on real Simulink data and the full pipeline has been run live
end-to-end against real Postgres. Read this before touching
`anomaly_detection.py`, `decision_layer.py`, `main.py`'s `/api/islanding`
route, or either training script.

## Current state (2026-07-20)

**Both training pipelines are real, not synthetic, and passing their
gates:**

- **Per-load Isolation Forests** (3.7.1): trained on 18,883 real
  confirmed-normal samples per load, sourced from `historic_grid_data`
  (backfilled from real Simulink exports - see "Real training data" below).
  Live in `isolation_forest_models/load_<id>.joblib`.
- **Grid-state Random Forest** (3.7.2): trained on 59,362 real labeled
  rows. NFS-6 (recall ≥ 0.90 on critical/fault_imminent): **0.988 / 0.979,
  passed without `--force`**. NFS-7 (accuracy ≥ 0.85): **0.984**. Live in
  `models/rf_grid_state.joblib`, auto-loaded by `decision_layer.classify_grid_state()`
  with zero code changes, exactly as designed.
- Confirmed live: a real HTTP request through a running `uvicorn` server,
  against real Postgres, correctly ran both trained models, classified the
  grid, chose a switching action, and persisted to `historic_grid_data`,
  `anomaly_scores`, `decisions`, and `grid_states`.
- `smoke_test.py` and the 66-test pytest suite both pass.

## Real training data - where it came from and how it was processed

Source: `Data Dumps/Data Dumps/*.csv` (33 files, Simulink digital-twin
exports, Section 3.5) - added to the repo 2026-07-17/18.

**Two unit mismatches in the raw exports, handled during loading, not
upstream**: `FaultProbability` and `SOC1`/`SOC2` are on a 0-100 scale in
the CSVs; every threshold in `decision_layer.py` (`_STATE_THRESHOLDS`,
`CRITICAL_SHED_TRIGGERS`) assumes 0-1. Missing this would have silently
broken every derived label and every shedding decision trained against
this data.

Pipeline, in order (each step's script, run once so far):

1. **`load_simulink_data.py`** - reads all 33 CSVs, reshapes the 5 loads'
   columns into `historic_grid_data` rows (297,165 total), and derives a
   5-way `grid_state` label per timestep to backfill `grid_states` (59,433
   rows) via `classify()`. Each file gets a distinct day-spaced base
   timestamp (last ~300 days) so `historic_grid_data`'s `(time, load_id)`
   primary key can't collide across files that otherwise all start at the
   same in-file `Timestamp`.
   - **Label derivation - confirmed with the team (2026-07-20).** The raw
     data only has a binary `Islanded` flag, not the full 5-way label
     Section 3.7.2 needs. `classify()` derives the other 4 states by
     running `FaultProbability/100` through the *same* threshold bins
     `decision_layer._classify_with_thresholds` already uses for its
     rule-based fallback, with `Islanded=1` overriding to `ISLANDED`
     regardless of the threshold - consistent with the doc's stated
     equivalence ("the anomaly score already stands in for the fault
     probability signal," Section 3.5), but still *my* interpretation of a
     labeling process the doc never spells out precisely, since nothing in
     the Simulink export labels those 4 states directly. Team decided this
     is good enough to proceed on: regenerating a dataset with real
     per-state ground truth (e.g. exporting a real 5-way label from
     Simulink's own Load Shedding Logic block, if it computes one
     internally) isn't worth the time on the team's current schedule. If
     that changes, or if NFS-6 numbers look wrong once real hardware data
     starts flowing, this is the first place to revisit.
2. **`retrain_isolation_forests.py`** - now runs for real against
   `historic_grid_data`. Confirmed the `LATERAL JOIN` fault-window filter
   (previously unverified against real Postgres) works correctly: excludes
   samples recorded while the grid was `critical`/`fault_imminent`/`islanded`
   at that moment, keeps everything else.
3. **`build_rf_training_csv.py`** (new) - re-scores every row from the raw
   CSVs through the just-retrained per-load models, using `main.py`'s
   exact production logic (`normalize_anomaly_score`, disconnected loads
   get a neutral placeholder rather than being scored), and assembles
   `rf_training_data.csv` in `train_decision_model.py`'s expected column
   shape.
   - Found and fixed a real bug here: `main.normalize_anomaly_score` uses
     Python's builtin `min`/`max`, which silently treat `NaN` as neither
     greater nor smaller than anything (`max(0.0, float('nan')) == 0.0`,
     not `NaN`). 71 rows where all 5 loads were simultaneously disconnected
     produced a `NaN` raw system score that would have silently come out as
     `system_anomaly_score = 0.0` - indistinguishable from a perfectly
     healthy reading - instead of being excluded as "no signal." Not a
     reachable bug in `main.py` itself (it always short-circuits on
     `raw_system_score is None` before calling `normalize_anomaly_score`,
     and `None` isn't `NaN`), but real here since `np.nanmean` produces
     float `NaN`. Fixed with an explicit all-disconnected check; regression
     test in `tests/test_build_rf_training_csv.py`.
4. **`train_decision_model.py rf_training_data.csv`** - trains and saves
   the real model (results above).

**`smoke_test.py` fix, found while re-running it after training the real
RF**: its hand-constructed scenarios (uniform `0.2` anomaly score per load
regardless of the scenario's system-wide score) got classified differently
by the real trained model than by the rule-based fallback it was written
against - not a bug in the model, just proof real training data doesn't
contain that system/per-load mismatch, so the RF never learned to expect
it. `smoke_test.py` now explicitly forces the rule-based fallback
(`decision_layer._rf_bundle = None`), so it stays a deterministic
regression check of `decision_layer.py`'s own documented threshold
behavior regardless of what model happens to be sitting in `models/` on
whatever machine runs it.

## Reproducing this from scratch

```powershell
python load_simulink_data.py            # ~1 min, populates historic_grid_data + grid_states
python retrain_isolation_forests.py     # ~10s, trains per-load Isolation Forests
python build_rf_training_csv.py         # ~10s, scores real data through those models
python train_decision_model.py rf_training_data.csv   # trains + gates the grid-state RF
```

Re-running `load_simulink_data.py` a second time will duplicate all rows
(no idempotency guard - it's a one-time bulk load, not designed to be
re-run against data that's already there). `TRUNCATE historic_grid_data,
grid_states;` first if you need to reload.

## Earlier integration work (condensed)

Before real data existed, this doc tracked: the app failing to start at
all (`load_data.py`'s undefined `conn`), no endpoint connecting the two ML
layers, a `load_id` type mismatch that would have silently disabled staged
critical-load shedding, missing `node_data`/`historic_grid_data` tables,
`anomaly_scores` never being written to, several crash-on-edge-case bugs
(all-disconnected payloads, unrecognized `load_id`s, no trained model
existing yet), and a score-scale mismatch between the two layers. All
fixed - see git history / the test suite (`tests/`, 66 tests) for details
if needed; not re-documented here now that the higher-priority open items
are the ones above.

## Still open

- **SOC ingestion.** `main.py::get_current_soc()` still defaults to `1.0`
  when `battery_status` is empty, which it is - the Figure 6 JSON payload
  doesn't carry SOC, and nothing writes to that table yet from live
  hardware. Doesn't affect training (the CSVs have real SOC1/SOC2), only
  live inference.
- **Rated current placeholders** for everything except Critical Load 2
  (`init-db/004_node_data.sql`) are still order-of-magnitude estimates, not
  datasheet/measured values.
- **LAN/public sharing setup** (`RUN_GUIDE.md` step 12) - firewall rules
  given, not yet confirmed working from a genuinely separate device.
