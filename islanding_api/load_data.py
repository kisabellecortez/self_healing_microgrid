import psycopg2
import os
import joblib

cur = conn.cursor()

def load_models():
    global models
    
    models = {}

    model_directory = "isolation_forest_models"

    for filename in os.listdir(model_directory):
        if filename.endswith(".joblib"):
            load_id = int(filename.replace("load_", "").replace(".joblib", ""))

            model = joblib.load(os.path.join(model_directory, filename))

            models[load_id] = model

    return models

def load_params():
    global load_metadata
    
    load_metadata = {}

    cur.execute(
        """
            SELECT load_id, rated_voltage, rated_current, critical
            FROM load_metadata
        """
    )

    data = cur.fetchall()

    for load_id, rated_voltage, rated_current, critical in data:
        load_metadata[load_id] = {
            "rated_voltage": rated_voltage,
            "rated_current": rated_current,
            "critical": critical
        }

    return load_metadata