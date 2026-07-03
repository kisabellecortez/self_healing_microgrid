```# Postgres setup (islanding_api)

TimescaleDB (Postgres + a time-series extension) running in Docker, plus the
SQLAlchemy layer to talk to it from FastAPI. Schema is built directly from
Table 1/2 of the AEGIS spec so it covers both the anomaly detection and
decision-making layers.

## 1. Start the database

```bash
cp .env.example .env
docker compose up -d
```

This pulls `timescale/timescaledb:latest-pg16`, creates the `aegis` database,
and auto-runs `init-db/001_schema.sql` on first boot (only happens once - if
you change the schema later, either drop the volume with
`docker compose down -v` or apply the change manually with `psql`/a migration).

Adminer (DB browser UI) is included at `http://localhost:8080` - server
`db`, user/pass/db from `.env`. Delete that block in `docker-compose.yml` if
you don't want it running.

## 2. Install Python deps

```bash
````````````````````````pip install "sqlalchemy>=2.0" asyncpg python-dotenv````````````````````````
```

## 3. Drop `app/database.py` and `app/models.py` into your FastAPI app

Adjust the import paths if your package isn't called `app`. Example usage in
a route:

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import Decision, GridState

router = APIRouter()

@router.post("/decisions")
async def log_decision(action: str, grid_state: GridState, latency_ms: float,
                        db: AsyncSession = Depends(get_db)):
    db.add(Decision(grid_state=grid_state, action=action, latency_ms=latency_ms))
    await db.commit()
    return {"status": "logged"}
```

## Schema overview

| Table | Spec refs | Owner |
|---|---|---|
| `feature_readings` | FS-6, FS-7, FS-8, FS-11, FS-12 | shared (MCU feature stream) |
| `anomaly_scores` | FS-13, FS-14 | anomaly detection layer |
| `grid_states` | FS-16, FS-17, FS-18 | grid state classification |
| `decisions` | FS-19, FS-20, FS-21, NFS-9 | decision-making layer |
| `battery_status` | FS-4, FS-5 | power layer |
| `load_status` | FS-9, FS-10, FS-22 | load subsystem |

All tables are TimescaleDB hypertables partitioned on `time`, since NFS-1 and
FS-11 both call this out as a time-series store.

## Notes / gotchas already handled for you

- **Enum values**: `GridState`/`LoadType` are Python `str` enums, but
  SQLAlchemy by default persists the enum *name* (`"WARNING"`) not the
  *value* (`"warning"`). The Postgres enum types only have the lowercase
  values, so `models.py` sets `values_callable` to force lowercase - don't
  remove that or inserts will fail.
- **Timezones**: every `time` column is `TIMESTAMPTZ`. The models use
  `DateTime(timezone=True)` explicitly - if you add a new datetime column,
  do the same, or asyncpg will reject timezone-aware Python `datetime`s.
- **Schema source of truth is the SQL file, not the ORM.** Don't call
  `Base.metadata.create_all()` - hypertable conversion
  (`create_hypertable(...)`) can't be expressed through SQLAlchemy, so the
  real schema lives in `init-db/001_schema.sql`.

## Later, if the schema starts changing a lot

Worth adding Alembic for versioned migrations instead of hand-editing the
init SQL. Happy to set that up when you get there - didn't include it now to
keep this minimal.
