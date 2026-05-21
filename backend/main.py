import logging

from fastapi import FastAPI, APIRouter, Request
from fastapi.middleware.cors import CORSMiddleware
from db import execute_query
import time

from geo_density_analysis import router as geo_router
from review_analytics import router as analytics_router
from cuisine_analytics import router as cuisine_router
from demand_forecast import router as demand_router
from border import router as border_router
from market_forecast import router as market_router

logging.basicConfig(level=logging.INFO)


app = FastAPI()
app.include_router(border_router)
app.include_router(geo_router)
app.include_router(analytics_router)
app.include_router(cuisine_router)
app.include_router(demand_router)
app.include_router(market_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # for dev only
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = (time.time() - start) * 1000
    logging.info(f"{request.method} {request.url.path} took {duration:.2f} ms")
    return response

@app.get("/")
def read_root():
    return {"status": "ok"}

@app.get("/restaurants")
def get_restaurants():
    rows = execute_query("SELECT * FROM restaurants;", fetch=True)
    return rows
