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
together on your machine - not just in my sandbox:

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

## 8. (Optional) Run the training script

Still using synthetic placeholder data until Simulink data lands:

```powershell
python train_decision_model.py
```

Real usage later: `python train_decision_model.py path\to\simulink_export.csv`

## 9. Run the API itself

```powershell
uvicorn main:app --reload
```

Then POST a Figure-6-shaped payload to `/api/islanding` to run both ML
layers end to end, e.g.:

```powershell
curl -X POST http://127.0.0.1:8000/api/islanding -H "Content-Type: application/json" -d '{\"timestamp\":\"2026-07-08T15:30:00Z\",\"weather\":{\"temperature\":28.4,\"humidity\":65,\"rainfall\":0.0,\"windspeed\":12.5},\"loads\":[{\"load_id\":1,\"voltage\":12.0,\"current\":0.3,\"power\":3.6,\"state\":1}]}'
```

---
