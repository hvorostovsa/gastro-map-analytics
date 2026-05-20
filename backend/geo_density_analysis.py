from fastapi import APIRouter, Query
from db import execute_query
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/geo", tags=["geo"])


@router.get("/heatmap")
def heatmap(bbox: str, zoom: int = 12):

    south, west, north, east = map(float, bbox.split(","))

    if south > north:
        south, north = north, south
    if west > east:
        west, east = east, west

    grid_size = max(0.0005, min(1.0, 360 / (2 ** zoom * 256)))

    sql = """
        SELECT
            CASE
                WHEN %(zoom)s >= 16 THEN latitude
                ELSE FLOOR(latitude / %(grid)s) * %(grid)s
            END AS lat,

            CASE
                WHEN %(zoom)s >= 16 THEN longitude
                ELSE FLOOR(longitude / %(grid)s) * %(grid)s
            END AS lon,

            CASE
                WHEN %(zoom)s >= 16 THEN 1
                ELSE COUNT(*)
            END AS intensity
        FROM businesses
        WHERE latitude BETWEEN %(south)s AND %(north)s
          AND longitude BETWEEN %(west)s AND %(east)s
        GROUP BY lat, lon
    """

    params = {
        "zoom": zoom,
        "grid": grid_size,
        "south": south,
        "north": north,
        "west": west,
        "east": east,
    }

    rows = execute_query(sql, params=params, fetch=True)

    return [
        {"lat": r[0], "lon": r[1], "intensity": r[2]}
        for r in rows or []
    ]

