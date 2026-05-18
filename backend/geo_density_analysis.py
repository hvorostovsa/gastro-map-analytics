from fastapi import APIRouter, Query
from db import execute_query
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/geo", tags=["geo"])


@router.get("/heatmap")
def heatmap(bbox: str, zoom: int = 12):

    south, west, north, east = map(float, bbox.split(","))

    if south > north:
        south, north = north, south
    if west > east:
        west, east = east, west

    grid_size = max(0.0005, min(1.0, 360 / (2 ** zoom * 256)))

    sql = f"""
        SELECT
            CASE
                WHEN {zoom} >= 16 THEN latitude
                ELSE FLOOR(latitude / {grid_size}) * {grid_size}
            END AS lat,

            CASE
                WHEN {zoom} >= 16 THEN longitude
                ELSE FLOOR(longitude / {grid_size}) * {grid_size}
            END AS lon,

            CASE
                WHEN {zoom} >= 16 THEN 1
                ELSE COUNT(*)
            END AS intensity
        FROM businesses
        WHERE latitude BETWEEN {south} AND {north}
          AND longitude BETWEEN {west} AND {east}
        GROUP BY lat, lon
    """
    
    rows = execute_query(sql, fetch=True)

    return [
        {
            "lat": r[0],
            "lon": r[1],
            "intensity": r[2],
        }
        for r in rows or []
    ]
