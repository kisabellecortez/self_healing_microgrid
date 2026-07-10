from fastapi import FastAPI
from apscheduler.schedulers.background import BackgroundScheduler
import subprocess
import sys
import os
import joblib
from load_data import load_models, load_params
import load_data

app = FastAPI()

scheduler = BackgroundScheduler()

def run_training_script():
    try:
        subprocess.run(
            [
                sys.executable,
                "isolation_forest_models_manager.py"
            ],
            check=True
        )

        load_data.models = load_models()

    except subprocess.CalledProcessError as e:
        print("Training failed: ", e.stderr)

@app.on_event("startup")
def startup_event():
    load_data.models = load_models()
    load_data.load_metadata = load_params()

    scheduler.add_job(
        run_training_script,
        "cron",
        hour=3,
        minute=0,
        id="daily_retraining",
        replace_existing=True
    )

    scheduler.start()

@app.on_event("shutdown")
def shutdown_event():
    scheduler.shutdown() 

@app.get("/")
def home():
    return {"message": "FastAPI is running."}

@app.get("/api/data")
def data():
    return {"message": "Database data insertion."}

@app.get("/api/islanding")
def islanding():
    return  {"message": "ML Pipeline"}