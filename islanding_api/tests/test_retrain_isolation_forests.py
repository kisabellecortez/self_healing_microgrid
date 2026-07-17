"""retrain_isolation_forests.py's DB queries (fetch_node_data,
fetch_training_samples) are tested here against a fake DB that records what
SQL text/params it was called with - this can confirm the right tables/
bindparams/bad-state list are wired in, but can't verify Postgres-specific
behavior (the LATERAL join, enum comparison) without a live database. Run
smoke_test.py (or a manual query) against real Postgres once Docker is up to
confirm fetch_training_samples' LATERAL join actually excludes fault-time
samples as intended - see NEXT_STEPS.md.
"""

from datetime import datetime

import joblib

import retrain_isolation_forests as rif


def test_train_one_fits_and_saves_a_model(tmp_path, monkeypatch):
    monkeypatch.setattr(rif, "MODEL_DIR", str(tmp_path))

    # (voltage, current, temperature, humidity, wind_speed, rainfall) - the
    # shape fetch_training_samples() is documented to return.
    samples = [(12.0 + i * 0.01, 0.30 + i * 0.001, 22.0, 55.0, 8.0, 0.0) for i in range(50)]
    ratings = {"rated_voltage": 12.0, "rated_current": 0.30}

    model = rif.train_one(load_id=1, samples=samples, ratings=ratings)

    saved_path = tmp_path / "load_1.joblib"
    assert saved_path.exists()
    reloaded = joblib.load(saved_path)
    # Model should be usable on the same 7-feature vector shape process_json builds.
    reloaded.predict([[3.6, 0.0, 0.0, 22.0, 55.0, 8.0, 0.0]])
    assert model is not None


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    """Records every (sql_text, params) execute() was called with, and
    returns canned rows - lets tests assert on what query
    fetch_training_samples/fetch_node_data actually issue without a real
    Postgres to run a LATERAL join against."""

    def __init__(self, rows=None):
        self.calls = []
        self._rows = rows or []

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        return _FakeResult(self._rows)


async def test_fetch_node_data_maps_rows_to_ratings_dict():
    db = _FakeDB(rows=[(1, 12.0, 0.30), (2, 12.0, 0.917)])

    result = await rif.fetch_node_data(db)

    assert result == {
        1: {"rated_voltage": 12.0, "rated_current": 0.30},
        2: {"rated_voltage": 12.0, "rated_current": 0.917},
    }
    assert "node_data" in db.calls[0][0]


async def test_fetch_training_samples_queries_historic_grid_data_with_bindparams():
    db = _FakeDB(rows=[(12.0, 0.3, 22.0, 55.0, 8.0, 0.0)])
    start_day = datetime(2026, 1, 1)

    result = await rif.fetch_training_samples(db, load_id=3, start_day=start_day)

    assert result == [(12.0, 0.3, 22.0, 55.0, 8.0, 0.0)]
    sql, params = db.calls[0]
    assert "historic_grid_data" in sql
    assert "LATERAL" in sql
    assert params == {"load_id": 3, "start_day": start_day}
    # Regression check: every bad state actually made it into the inlined
    # NOT IN list - a typo here would silently let fault-time samples
    # through into training instead of excluding them.
    for state in rif._BAD_GRID_STATES:
        assert f"'{state}'" in sql


async def test_retrain_all_skips_loads_with_no_confirmed_normal_samples(monkeypatch, tmp_path):
    monkeypatch.setattr(rif, "MODEL_DIR", str(tmp_path))

    async def fake_fetch_node_data(db):
        return {1: {"rated_voltage": 12.0, "rated_current": 0.30}}

    async def fake_fetch_training_samples(db, load_id, start_day):
        return []  # nothing confirmed-normal yet for this load

    trained = []
    monkeypatch.setattr(rif, "fetch_node_data", fake_fetch_node_data)
    monkeypatch.setattr(rif, "fetch_training_samples", fake_fetch_training_samples)
    monkeypatch.setattr(rif, "train_one", lambda *a, **kw: trained.append((a, kw)))

    class _NullSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(rif, "AsyncSessionLocal", lambda: _NullSession())

    await rif.retrain_all()

    # Must not call train_one on an empty sample set - IsolationForest.fit([])
    # would raise, and a load with no history yet isn't an error, just nothing to do.
    assert trained == []
