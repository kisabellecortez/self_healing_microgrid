"""load_data.py used to fail at import time (`cur = conn.cursor()` with
`conn` never defined) - these tests cover the rewritten version's two
responsibilities: loading trained per-load models off disk, and loading
per-load reference data from the load_metadata table.
"""

import joblib

import load_data


def test_load_models_returns_empty_dict_when_directory_missing(tmp_path):
    result = load_data.load_models(model_dir=str(tmp_path / "does_not_exist"))
    assert result == {}


def test_load_models_loads_joblib_files_keyed_by_load_id(tmp_path):
    joblib.dump({"fake": "model-1"}, tmp_path / "load_1.joblib")
    joblib.dump({"fake": "model-2"}, tmp_path / "load_2.joblib")
    (tmp_path / "not_a_model.txt").write_text("ignore me")

    result = load_data.load_models(model_dir=str(tmp_path))

    assert set(result) == {1, 2}
    assert result[1] == {"fake": "model-1"}


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, stmt):
        return _FakeResult(self._rows)


async def test_load_params_maps_rows_by_load_id_with_critical_flag():
    rows = [
        (1, "critical_1", 12.0, 0.30, "critical"),
        (4, "noncritical_1", 12.0, 0.30, "non_critical"),
    ]
    result = await load_data.load_params(_FakeDB(rows))

    assert result[1] == {"name": "critical_1", "rated_voltage": 12.0, "rated_current": 0.30, "critical": True}
    assert result[4]["critical"] is False
