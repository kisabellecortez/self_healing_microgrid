"""Integration tests for the POST /api/islanding endpoint in main.py - the
bridge between anomaly_detection.py (3.7.1) and decision_layer.py (3.7.2).

Uses a fake async DB session (no Docker/Postgres required) so this suite can
run anywhere, including CI without a database. smoke_test.py is still the
one to run against a real Postgres instance before trusting the logged-data
path end to end (see RUN_GUIDE.md).

TestClient(main.app) is used WITHOUT the `with` context manager on purpose:
that skips main.py's startup event (which would otherwise try to open a real
DB connection via load_data.load_params()). load_data.models/load_metadata
are monkeypatched directly instead, which is exactly the state startup would
have produced from a real load_metadata table.
"""

import pytest
from fastapi.testclient import TestClient

import load_data
import main
from database import get_db
from decision_layer import Action


class _FakeResult:
    def __init__(self, row=None):
        self._row = row

    def first(self):
        return self._row

    def all(self):
        return [] if self._row is None else [self._row]


class FakeAsyncSession:
    """Stands in for the real AsyncSession: records everything add()ed,
    no-ops commit(), and returns "no rows" for every execute() - which
    exercises get_current_soc()'s and time_since_islanding_started()'s
    documented defaults (soc=1.0, time_islanded_sec=0.0)."""

    def __init__(self):
        self.added = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def execute(self, stmt):
        return _FakeResult(None)


LOAD_METADATA = {
    1: {"name": "critical_1", "rated_voltage": 12.0, "rated_current": 0.30, "critical": True},
    2: {"name": "critical_2", "rated_voltage": 12.0, "rated_current": 0.917, "critical": True},
    3: {"name": "critical_3", "rated_voltage": 12.0, "rated_current": 0.20, "critical": True},
    4: {"name": "noncritical_1", "rated_voltage": 12.0, "rated_current": 0.30, "critical": False},
    5: {"name": "noncritical_2", "rated_voltage": 12.0, "rated_current": 0.20, "critical": False},
}


class _FakeIsolationForest:
    def __init__(self, score):
        self._score = score

    def decision_function(self, X):
        return [self._score]

    def predict(self, X):
        return [1 if self._score >= 0 else -1]


def _payload(load_states=None):
    load_states = load_states or {i: 1 for i in range(1, 6)}
    return {
        "timestamp": "2026-07-08T15:30:00Z",
        "weather": {"temperature": 28.4, "humidity": 65, "rainfall": 0.0, "windspeed": 12.5},
        "loads": [
            {"load_id": lid, "voltage": 12.0, "current": 0.3, "power": 3.6, "state": state}
            for lid, state in load_states.items()
        ],
    }


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(load_data, "load_metadata", LOAD_METADATA)
    monkeypatch.setattr(load_data, "models", {i: _FakeIsolationForest(0.3) for i in range(1, 6)})

    fake_db = FakeAsyncSession()

    async def override_get_db():
        yield fake_db

    main.app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(main.app), fake_db
    finally:
        main.app.dependency_overrides.clear()


def test_islanding_endpoint_maps_numeric_load_id_to_decision_layer_names(client):
    test_client, fake_db = client
    resp = test_client.post("/api/islanding", json=_payload())

    assert resp.status_code == 200
    body = resp.json()
    # This is the regression case for the load_id mismatch: anomaly_detection.py
    # keys everything by the JSON payload's numeric load_id (1-5),
    # decision_layer.py keys everything by name ("critical_1", ...) - if the
    # endpoint didn't translate between them, build_load_signals() would
    # KeyError or CRITICAL_SHED_TRIGGERS lookups would silently never match.
    assert set(body["actions"]) == {"critical_1", "critical_2", "critical_3", "noncritical_1", "noncritical_2"}
    assert body["grid_state"] in {"normal", "warning", "critical", "fault_imminent", "islanded"}


def test_islanding_endpoint_persists_anomaly_scores(client):
    test_client, fake_db = client
    test_client.post("/api/islanding", json=_payload())

    from models import AnomalyScore
    scored_nodes = {row.node_id for row in fake_db.added if isinstance(row, AnomalyScore)}
    # One row per connected load (by name, not raw int id) plus one system-wide row.
    assert scored_nodes == {"critical_1", "critical_2", "critical_3", "noncritical_1", "noncritical_2", None}
    assert fake_db.commits >= 1


def test_islanding_endpoint_disconnected_load_gets_neutral_score_not_keyerror(client):
    test_client, _ = client
    load_states = {i: 1 for i in range(1, 6)}
    load_states[4] = 0  # noncritical_1 disconnected - never scored by process_json
    resp = test_client.post("/api/islanding", json=_payload(load_states))

    assert resp.status_code == 200
    assert "noncritical_1" in resp.json()["actions"]


def test_islanding_endpoint_persists_historic_grid_data_for_every_recognized_load(client):
    test_client, fake_db = client
    test_client.post("/api/islanding", json=_payload())

    from models import HistoricGridData
    rows = {row.load_id: row for row in fake_db.added if isinstance(row, HistoricGridData)}
    # One row per load in the payload (Section 3.7.1 retraining source),
    # keyed by the numeric load_id - unlike AnomalyScore, this table is
    # indexed by node_data.load_id directly (it's the FK target), not name.
    assert set(rows) == {1, 2, 3, 4, 5}
    assert rows[1].state is True
    assert rows[1].power == pytest.approx(12.0 * 0.3)
    assert rows[1].voltage_deviation == pytest.approx(12.0 - 12.0)


def test_islanding_endpoint_historic_grid_data_nulls_deviation_for_disconnected_load(client):
    test_client, fake_db = client
    load_states = {i: 1 for i in range(1, 6)}
    load_states[4] = 0  # noncritical_1 disconnected
    test_client.post("/api/islanding", json=_payload(load_states))

    from models import HistoricGridData
    rows = {row.load_id: row for row in fake_db.added if isinstance(row, HistoricGridData)}
    # Still logged (connection history matters even when disconnected), but
    # deviation from a rating is meaningless with no current flowing - NULL,
    # not a deviation computed against a raw 0.
    assert rows[4].state is False
    assert rows[4].power is None
    assert rows[4].voltage_deviation is None
    assert rows[4].current_deviation is None


def test_islanding_endpoint_all_loads_disconnected_short_circuits(client):
    test_client, fake_db = client
    resp = test_client.post("/api/islanding", json=_payload({i: 0 for i in range(1, 6)}))

    assert resp.status_code == 200
    body = resp.json()
    assert body["grid_state"] is None
    assert body["actions"] == {}
    # No system-wide anomaly score to log, and no decision was made -
    # log_decision() must not have been called.
    from models import Decision
    assert not any(isinstance(row, Decision) for row in fake_db.added)


def test_islanding_endpoint_unknown_load_id_is_ignored_not_crashed(client, monkeypatch):
    # A load_id with no load_metadata row (e.g. hardware sends an id nobody
    # seeded yet) must not crash the whole request.
    monkeypatch.setattr(load_data, "models", {**{i: _FakeIsolationForest(0.3) for i in range(1, 6)}, 99: _FakeIsolationForest(0.1)})
    test_client, fake_db = client
    payload = _payload()
    payload["loads"].append({"load_id": 99, "voltage": 12.0, "current": 0.1, "power": 1.2, "state": 1})

    resp = test_client.post("/api/islanding", json=payload)
    assert resp.status_code == 200
    assert "99" not in resp.json()["actions"]

    # Also must not attempt a historic_grid_data row for load_id 99 - that
    # column has a hard FK to node_data(load_id) (init-db/005), so trying
    # would fail the whole request's commit(), not just skip harmlessly.
    from models import HistoricGridData
    assert not any(isinstance(row, HistoricGridData) and row.load_id == 99 for row in fake_db.added)


def test_normalize_anomaly_score_bounds():
    from main import normalize_anomaly_score
    assert normalize_anomaly_score(0.5) == 0.0
    assert normalize_anomaly_score(-0.5) == 1.0
    assert normalize_anomaly_score(-10) == 1.0   # clipped
    assert normalize_anomaly_score(10) == 0.0    # clipped
    assert normalize_anomaly_score(0.0) == 0.5


async def test_get_current_soc_defaults_to_full_when_no_battery_rows():
    from main import get_current_soc
    soc = await get_current_soc(FakeAsyncSession())
    assert soc == 1.0
