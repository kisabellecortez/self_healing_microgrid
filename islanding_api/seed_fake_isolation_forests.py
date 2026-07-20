"""Seeds placeholder per-load Isolation Forest models from synthetic data,
so /api/islanding (and the rest of the pipeline) can actually be exercised
end to end before real historic_grid_data accumulates enough history for
retrain_isolation_forests.py to train on for real.

NOT a substitute for real training - these models only know "normal" as a
tight cluster around each load's rated voltage/current from node_data; they
will not meaningfully distinguish real anomalies from real sensor noise.
Delete isolation_forest_models/ and re-run retrain_isolation_forests.py
once real historic_grid_data exists - reuses the exact same train_one() so
nothing else in the pipeline needs to change when that happens.

Usage:
    python seed_fake_isolation_forests.py
"""

import asyncio

import numpy as np

from database import AsyncSessionLocal
from retrain_isolation_forests import fetch_node_data, train_one

N_SAMPLES = 500


def make_synthetic_samples(rated_voltage: float, rated_current: float, seed: int) -> list[tuple]:
    """(voltage, current, temperature, humidity, wind_speed, rainfall) tuples
    clustered tightly around the load's rating - the same shape
    fetch_training_samples() returns from historic_grid_data."""
    rng = np.random.default_rng(seed)
    voltage = rng.normal(rated_voltage, rated_voltage * 0.02, N_SAMPLES)
    current = rng.normal(rated_current, rated_current * 0.05, N_SAMPLES)
    temperature = rng.normal(22, 5, N_SAMPLES)
    humidity = rng.normal(55, 10, N_SAMPLES)
    wind_speed = rng.normal(8, 4, N_SAMPLES)
    rainfall = np.abs(rng.normal(0, 1, N_SAMPLES))
    return list(zip(voltage, current, temperature, humidity, wind_speed, rainfall))


async def main():
    async with AsyncSessionLocal() as db:
        ratings_by_load = await fetch_node_data(db)

    if not ratings_by_load:
        print("node_data is empty - apply init-db/004_node_data.sql first.")
        return

    for load_id, ratings in ratings_by_load.items():
        samples = make_synthetic_samples(ratings["rated_voltage"], ratings["rated_current"], seed=load_id)
        train_one(load_id, samples, ratings)
        print(f"seeded load_id={load_id}: {len(samples)} synthetic samples -> isolation_forest_models/load_{load_id}.joblib")


if __name__ == "__main__":
    asyncio.run(main())
