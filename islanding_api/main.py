from fastapi import FastAPI
from apscheduler.schedulers.background import BackgroundScheduler
import subprocess
import sys

app = FastAPI()

scheduler = BackgroundScheduler()

def run_training_script():
    try:
        subprocess.run(
            [
                sys.executable,
                "retrain_isolation_forests.py"
            ]
        )

    except subprocess.CalledProcessError as e:
        print("Training failed: ", e.stderr)

@app.on_event("startup")
def startup_event():
    scheduler.start()

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