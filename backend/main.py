from fastapi import FastAPI
from db import execute_query

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "ok"}

@app.get("/restaurants")
def get_restaurants():
    rows = execute_query("SELECT * FROM restaurants;", fetch=True)
    return rows
