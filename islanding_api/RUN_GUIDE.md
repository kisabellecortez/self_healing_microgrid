# Run Guide (Windows / PowerShell)

Everything needed to get `islanding_api` running from a clean checkout, in
order. 

## 1. Start Docker Desktop

Open it, wait for "Engine running" in the bottom left. Everything below
fails immediately if this step is skipped.

## 2. Start the database

```powershell
cd "C:\ECE 498\self_healing_microgrid\islanding_api"
docker compose up -d
```

First run pulls the TimescaleDB image and auto-runs `init-db/001_schema.sql`.
Later runs just start the existing containers - fast.

## 3. Apply any pending migrations

Only needed once per new `.sql` file added to `init-db/` after your first
`docker compose up -d` (init scripts only auto-run against an empty volume).
Run these **in order**. `003_load_metadata.sql` is superseded by
`004_node_data.sql` and only needs applying if this database was already
running before 004 existed - `004` handles both cases (fresh DB, or
migrating an already-applied `003`) on its own, so skip straight to it on a
clean checkout:

```powershell
Get-Content init-db\002_decisions_features.sql | docker exec -i aegis_postgres psql -U aegis -d aegis
Get-Content init-db\004_node_data.sql | docker exec -i aegis_postgres psql -U aegis -d aegis
Get-Content init-db\005_historic_grid_data.sql | docker exec -i aegis_postgres psql -U aegis -d aegis
```

If that errors, use this instead (copies the file in directly, sidesteps
PowerShell stdin quirks entirely):

```powershell
docker cp init-db\002_decisions_features.sql aegis_postgres:/tmp/002.sql
docker exec -it aegis_postgres psql -U aegis -d aegis -f /tmp/002.sql
docker cp init-db\004_node_data.sql aegis_postgres:/tmp/004.sql
docker exec -it aegis_postgres psql -U aegis -d aegis -f /tmp/004.sql
docker cp init-db\005_historic_grid_data.sql aegis_postgres:/tmp/005.sql
docker exec -it aegis_postgres psql -U aegis -d aegis -f /tmp/005.sql
```

## 4. Verify the schema

```powershell
docker exec -it aegis_postgres psql -U aegis -d aegis -c "\d decisions"
docker exec -it aegis_postgres psql -U aegis -d aegis -c "SELECT * FROM node_data"
docker exec -it aegis_postgres psql -U aegis -d aegis -c "\d historic_grid_data"
```

`decisions` should show 8 columns including `features` and `load_actions`.
`node_data` should show 5 seeded rows (`critical_1`..`critical_3`,
`noncritical_1`..`noncritical_2`). `historic_grid_data` should exist as a
hypertable with a FK on `load_id` to `node_data`. If any is missing, step 3
didn't run yet - anomaly_detection.py and main.py's `/api/islanding`
endpoint both depend on `node_data`; `retrain_isolation_forests.py` depends
on `historic_grid_data` too.

## 5. Install Python dependencies

```powershell
pip install -r requirements.txt
```

(Or manually: `sqlalchemy>=2.0 asyncpg python-dotenv scikit-learn joblib
pandas apscheduler pydantic pytest pytest-asyncio httpx2`.)

## 6. Run the smoke test

Proves DB connectivity, all 5 decision branches, and DB logging all work
together against a real Postgres instance - confirmed working end to end
2026-07-17 (see `NEXT_STEPS.md`):

```powershell
python smoke_test.py
```

Expect `ALL CHECKS PASSED`. If it fails at step 1 (connectivity), `.env`
probably doesn't match what's in `docker-compose.yml`, or Docker isn't
running.

## 7. Run the pytest suite

No database required - these are pure-logic and mocked-DB tests covering
decision_layer.py, anomaly_detection.py, load_data.py, retrain_isolation_forests.py,
and the `/api/islanding` endpoint that connects the two ML layers together
(see `tests/` and `NEXT_STEPS.md`):

```powershell
pytest
```

Expect all tests green. Run this and `smoke_test.py` both before touching
`decision_layer.py`, `anomaly_detection.py`, or `main.py`'s `/api/islanding`
route.

## 8. Train on real data (or seed placeholders if you don't have it yet)

**Do this before step 9** - without a trained model, `/api/islanding` runs
but silently skips every load. As of 2026-07-20 there's real Simulink data
in `Data Dumps/Data Dumps/*.csv` and both training pipelines run on it for
real - see `NEXT_STEPS.md` for the full story (unit conversions, a couple
of real bugs found processing it, NFS-6/7 results).

**If you have that data** (real path, run in order):
```powershell
python load_simulink_data.py            # ~1 min - populates historic_grid_data + grid_states
python retrain_isolation_forests.py     # ~10s - trains real per-load Isolation Forests
python build_rf_training_csv.py         # ~10s - scores real data through those models
python train_decision_model.py rf_training_data.csv   # trains + gates the grid-state RF
```
Expect the Random Forest step to print recall/accuracy and confirm it met
NFS-6/NFS-7 without needing `--force` (it did, last run: 0.988/0.979 recall,
0.984 accuracy).

**If you don't have that data yet** (e.g. testing on a fresh checkout
without `Data Dumps/`), fall back to synthetic placeholders so the pipeline
still does something end to end:
```powershell
python seed_fake_isolation_forests.py   # fake per-load Isolation Forests
python train_decision_model.py          # fake grid-state Random Forest (no CSV arg)
```
Expect 5 lines from the first, and a "No CSV given" notice from the second.
**Not real training data** - delete `isolation_forest_models\` and
`models\rf_grid_state.joblib` and rerun the real path above once you have
`Data Dumps/`; nothing else needs to change either way, both scripts write
to the exact same paths `decision_layer.py`/`anomaly_detection.py`
auto-load from.

## 9. Run the API itself

```powershell
uvicorn main:app --reload
```

Binds to `127.0.0.1` only (uvicorn's default) - reachable from this machine
alone. See step 11 to make it reachable from a teammate's machine instead.

**If you ran step 8 (seeding models) after the server was already running,
restart it** - `load_data.models` is only loaded once, at startup
(`main.py`'s `startup_event`); it won't pick up new `.joblib` files on its
own.

Then POST a Figure-6-shaped payload to `/api/islanding` to run both ML
layers end to end. PowerShell's `curl` is aliased to `Invoke-WebRequest`,
which doesn't take bash-style `-X`/`-H`/`-d` flags - use this instead:

```powershell
$body = @{
    timestamp = "2026-07-08T15:30:00Z"
    weather = @{ temperature = 28.4; humidity = 65; rainfall = 0.0; windspeed = 12.5 }
    loads = @(
        @{ load_id = 1; voltage = 12.0; current = 0.3; power = 3.6; state = 1 }
    )
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/islanding" -Method Post -ContentType "application/json" -Body $body
```

Expect a JSON response with `grid_state`, `branch`, and per-load `actions`.

## 10. Verify the request actually landed in Postgres

```powershell
docker exec -it aegis_postgres psql -U aegis -d aegis -c "SELECT time, load_id, voltage, state FROM historic_grid_data ORDER BY time DESC LIMIT 5;"
docker exec -it aegis_postgres psql -U aegis -d aegis -c "SELECT time, node_id, anomaly_score FROM anomaly_scores ORDER BY time DESC LIMIT 5;"
docker exec -it aegis_postgres psql -U aegis -d aegis -c "SELECT time, grid_state, action FROM decisions ORDER BY time DESC LIMIT 3;"
```

Each should show a row matching your test request's timestamp.

## 11. Share the DB / API with a teammate

`ipconfig` on Windows lists multiple adapters - only one of them is the
right IP to hand out:

- **Wi-Fi adapter's IPv4** (e.g. `10.0.0.194`) - your real LAN IP. Use this
  for a teammate on the same WiFi/router as you (Scenario A below).
- **`vEthernet (WSL)` adapter** (e.g. `172.28.176.1`) - Docker Desktop's
  internal WSL2 network adapter, not a real network interface. Not
  reachable from any other device - ignore it for sharing.
- **Your public IP** (from a "what's my ip" site, e.g. `99.251.110.150`) -
  only relevant for a teammate on a *different* network entirely (Scenario
  B below), and only works alongside router-level port forwarding, which
  is a setting on your router's admin page, not something changeable from
  this machine or by me.

### Scenario A: teammate is on your WiFi/network

Docker already binds Postgres to all interfaces (`docker-compose.yml`'s
`"5432:5432"` → `0.0.0.0:5432`), so this is just a Windows Firewall +
host-binding problem.

1. **Open the ports** (PowerShell **as Administrator**, one-time):
   ```powershell
   New-NetFirewallRule -DisplayName "AEGIS Postgres (dev)" -Direction Inbound -Protocol TCP -LocalPort 5432 -Action Allow
   New-NetFirewallRule -DisplayName "AEGIS Adminer (dev)" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow
   New-NetFirewallRule -DisplayName "AEGIS API (dev)" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
   ```
2. **DB access** (browsing tables, running queries): share `<your-LAN-IP>:5432`
   plus the credentials in `.env` (defaults `aegis` / `aegis_dev_password` /
   db `aegis`). Easiest for a non-CLI teammate: **Adminer**, already running
   at `http://<your-LAN-IP>:8080` - just a browser, no client install. In
   Adminer's login form: System `PostgreSQL`, Server `db` (the
   docker-compose service name - Adminer runs in its own container, so
   `localhost` there would mean itself, not Postgres), then the
   username/password/database from `.env`.
3. **API access** (POSTing test payloads to your running `/api/islanding`
   directly): uvicorn must bind to all interfaces, not just `127.0.0.1` -
   restart it with:
   ```powershell
   uvicorn main:app --reload --host 0.0.0.0 --port 8000
   ```
   Then your teammate hits `http://<your-LAN-IP>:8000/api/islanding` from
   their own machine the same way step 9 shows.

### Scenario B: teammate is on a different network

Needs everything in Scenario A, **plus** port forwarding on your router
(your public IP, `99.251.110.150`, only reaches your machine if the router
forwards the relevant port to `10.0.0.194`) - log into your router's admin
page and forward whichever of these you actually need:

| External port | Forward to | Purpose |
|---|---|---|
| 8000 | `10.0.0.194:8000` | API access (`/api/islanding` etc.) |
| 8080 | `10.0.0.194:8080` | Adminer (DB browsing via web UI) |
| 5432 | `10.0.0.194:5432` | Direct Postgres client access |

**Only forward what you actually need.** In particular, think twice before
forwarding 5432 (raw Postgres) to the public internet - exposed Postgres
instances get scanned and brute-forced by bots within hours, and the
current credentials are the `docker-compose.yml` dev defaults
(`aegis`/`aegis_dev_password`), not something safe to leave reachable from
anywhere. If your teammate just needs to test the pipeline, forwarding only
8000 (the API) and having her hit `http://99.251.110.150:8000/api/islanding`
is enough and doesn't expose the database directly. If she genuinely needs
raw DB access from outside your network, change `POSTGRES_PASSWORD` in
`.env` (and restart the container) before forwarding 5432 or 8080.

---
