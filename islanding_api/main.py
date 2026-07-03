from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def home():
    return {"message": "FastAPI is running."}

@app.get("/api/data")
def data():
    return {"message": "Database data insertion."}

@app.get("/api/islanding")
def islanding():
    return  {"message": "ML Pipeline"}