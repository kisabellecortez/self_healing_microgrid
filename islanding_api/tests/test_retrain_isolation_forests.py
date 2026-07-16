"""retrain_isolation_forests.py is blocked on a real historic-data source
(see NEXT_STEPS.md) - these tests cover the parts that don't depend on that:
the pure model-fitting step, and that the still-unresolved data-fetch raises
clearly instead of hitting a nonexistent table.
"""

import asyncio

import joblib
import pytest

import retrain_isolation_forests as rif


def test_train_one_fits_and_saves_a_model(tmp_path, monkeypatch):
    monkeypatch.setattr(rif, "MODEL_DIR", str(tmp_path))

    # (voltage, current, temperature, humidity, windspeed, rainfall) - the
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


async def test_fetch_training_samples_raises_until_historic_source_exists():
    with pytest.raises(NotImplementedError):
        await rif.fetch_training_samples(db=None, load_id=1, start_day=None)
