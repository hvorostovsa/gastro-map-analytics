from fastapi import FastAPI, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from db import execute_query

from geo_density_analysis import router as geo_router
from review_analytics import router as analytics_router
from cuisine_analytics import router as cuisine_router

app = FastAPI()
app.include_router(geo_router)
app.include_router(analytics_router)
app.include_router(cuisine_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # for dev only
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"status": "ok"}

@app.get("/restaurants")
def get_restaurants():
    rows = execute_query("SELECT * FROM restaurants;", fetch=True)
    return rows
