"""Per-load isolation-forest models + reference metadata (Section 3.7.1).

`models` and `load_metadata` are populated once at FastAPI startup (see
main.py's startup_event) and kept as module-level dicts so anomaly_detection.py
can look both up by load_id without a DB round-trip on every request.

Previously this module built a synchronous psycopg2 connection via a bare
`conn.cursor()` with `conn` never defined, which raised NameError on import -
the app couldn't start. Rewritten to reuse the app's shared async SQLAlchemy
engine (database.py) instead, consistent with decision_layer.py/main.py, and
to query the load_metadata table (init-db/003_load_metadata.sql) rather than
`load_metadata` as a bare table name assumed to already exist.
"""

import os

import joblib
from sqlalchemy import text

# Anchored to this file's directory rather than a bare relative path, so
# lookup doesn't depend on the process's current working directory (differs
# between `python load_data.py`, `uvicorn main:app`, and pytest).
MODEL_DIR = os.path.join(os.path.dirname(__file__), "isolation_forest_models")

models: dict[int, object] = {}
load_metadata: dict[int, dict] = {}


def load_models(model_dir: str = MODEL_DIR) -> dict[int, object]:
    """Loads every load_<id>.joblib in model_dir into a {load_id: model} dict.

    Returns {} if the directory doesn't exist yet rather than raising -
    expected before the first retrain has run (see NEXT_STEPS.md), and
    anomaly_detection.anomaly_detection() will KeyError with a clear message
    for any load_id missing a model rather than this failing silently.
    """
    global models
    models = {}

    if not os.path.isdir(model_dir):
        return models

    for filename in os.listdir(model_dir):
        if filename.endswith(".joblib"):
            load_id = int(filename.removeprefix("load_").removesuffix(".joblib"))
            models[load_id] = joblib.load(os.path.join(model_dir, filename))

    return models


async def load_params(db) -> dict[int, dict]:
    """Loads per-load voltage_rating/current_rating/critical/name from the
    node_data table (init-db/004_node_data.sql) into a {load_id: dict}
    lookup, keyed the same way the embedded system's JSON payload keys loads
    (Figure 6, Section 3.6).

    Returned dict keys are unchanged from before node_data replaced
    load_metadata (still "rated_voltage"/"rated_current") - only the SQL
    source renamed, not this function's contract, so anomaly_detection.py
    didn't need to change.
    """
    global load_metadata
    load_metadata = {}

    result = await db.execute(text("""
        SELECT load_id, name, voltage_rating, current_rating, load_type
        FROM node_data
    """))
    for load_id, name, voltage_rating, current_rating, load_type in result.all():
        load_metadata[load_id] = {
            "name": name,
            "rated_voltage": voltage_rating,
            "rated_current": current_rating,
            "critical": load_type == "critical",
        }

    return load_metadata
