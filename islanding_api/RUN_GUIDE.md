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
Currently that's `002_decisions_features.sql`:

```powershell
Get-Content init-db\002_decisions_features.sql | docker exec -i aegis_postgres psql -U aegis -d aegis
```

If that errors, use this instead (copies the file in directly, sidesteps
PowerShell stdin quirks entirely):

```powershell
docker cp init-db\002_decisions_features.sql aegis_postgres:/tmp/002.sql
docker exec -it aegis_postgres psql -U aegis -d aegis -f /tmp/002.sql
```

## 4. Verify the schema

```powershell
docker exec -it aegis_postgres psql -U aegis -d aegis -c "\d decisions"
```

Should show 8 columns including `features` and `load_actions`. If those two
are missing, step 3 didn't run yet.

## 5. Install Python dependencies

```powershell
pip install "sqlalchemy>=2.0" asyncpg python-dotenv scikit-learn joblib pandas
```

## 6. Run the smoke test

Proves DB connectivity, all 5 decision branches, and DB logging all work
together on your machine - not just in my sandbox:

```powershell
python smoke_test.py
```

Expect `ALL CHECKS PASSED`. If it fails at step 1 (connectivity), `.env`
probably doesn't match what's in `docker-compose.yml`, or Docker isn't
running.

## 7. (Optional) Run the training script

Still using synthetic placeholder data until Simulink data lands:

```powershell
python train_decision_model.py
```

Real usage later: `python train_decision_model.py path\to\simulink_export.csv`

## 8. Run the API itself

```powershell
uvicorn main:app --reload
```

(Assumes `main.py` has a FastAPI `app` object, which it should already,
given the repo's initial "Set up FastAPI" commit. If `uvicorn` isn't
found: `pip install uvicorn`.)

---
