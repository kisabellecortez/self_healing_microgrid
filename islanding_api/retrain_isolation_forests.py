from datetime import datetime, timedelta
import psycopg2
from sklearn.ensemble import IsolationForest
import joblib
import os
import numpy as np

# isolation forest parameters
N_ESTIMATORS = 200
CONTAMINATION = 0.01
RANDOM_STATE = 42

# PostgreSQL connection
conn = psycopg2.connect(
    host="",
    database="",
    user="",
    password="",
    port=5432
)

cur = conn.cursor()

def retrain_isolation_forests(load_id, rated_voltage, rated_current):
    """
    Retrain all isolation forest algorithms automatically at the end of each day, 
    using data from grid_historic_data table using a rolling 12 month interval. 
    """
    prev_day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    start_day = prev_day - timedelta(days=365)

    cur.execute(
        """
            SELECT 
                voltage, 
                current, 
                temperature, 
                humidity,
                windspeed, 
                rainfall
            FROM historic_grid_data
            WHERE load = %s
            AND timestamp >= %s 
            AND state = TRUE
            AND fault = FALSE
        """,
        (load_id, start_day)
    )

    samples = cur.fetchall()

    training_dataset = []

    for sample in samples:
        (
            voltage, 
            current, 
            temperature, 
            humidity, 
            windspeed, 
            rainfall
        ) = sample
        
        power = sample.voltage * sample.current
        voltage_deviation = sample.voltage - rated_voltage
        current_deviation = sample.current - rated_current

        training_dataset.append([
            power,
            voltage_deviation,
            current_deviation,
            temperature, 
            humidity,
            windspeed,
            rainfall
        ])

        new_model = IsolationForest(
            n_estimators = N_ESTIMATORS,
            contamination = CONTAMINATION,
            random_state = RANDOM_STATE
        )

        new_model.fit(training_dataset)

        os.makedirs("isolation_forest_models", exist_ok=True)

        joblib.dump(
            new_model, 
            f"isolation_forest_models/load_{load_id}.joblib"
        )

        return new_model

cur.execute(
    """
        SELECT load_id, rated_voltage, rated_current
        FROM load_data
    """
)

load_data = cur.fetchall()

loads = {}

for load_id, rated_voltage, rated_current in load_data:
    loads[load_id] = {
        "rated_voltage": rated_voltage, 
        "rated_current": rated_current
    }

for load_id, values in loads.items():
    retrain_isolation_forests(
        load_id, 
        values["rated_voltage"],
        values["rated_current"]
    )