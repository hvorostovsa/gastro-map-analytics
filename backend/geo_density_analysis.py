from fastapi import APIRouter, Query
from db import execute_query
from typing import Optional
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


from fastapi import APIRouter, Query
from typing import Optional
import json


@router.get("/counties/density-choropleth")
def counties_density_choropleth(
    bbox: str = Query(...),
    zoom: int = Query(12),
    min_businesses: int = Query(0)  # Changed to 0 to include all counties
):
    south, west, north, east = map(float, bbox.split(","))
    
    sql = """
        SELECT 
            c.name AS county_name,
            c.state,
            ST_AsGeoJSON(c.geom) AS geojson,
            ST_Area(c.geom::geography) / 1000000 AS area_sqkm,
            COUNT(b.business_id) AS business_count
        FROM counties c
        LEFT JOIN businesses b ON b.county = c.name
        WHERE ST_Intersects(
            c.geom::geometry,
            ST_MakeEnvelope(%(west)s, %(south)s, %(east)s, %(north)s, 4326)
        )
        GROUP BY c.name, c.state, c.geom
        HAVING COUNT(b.business_id) >= %(min_businesses)s
    """
    
    rows = execute_query(sql, params={
        "south": south, "west": west, "north": north, "east": east,
        "min_businesses": min_businesses
    }, fetch=True)
    
    features = []
    densities = []
    
    for row in rows:
        county_name, state, geojson, area_sqkm, business_count = row
        area_sqkm = float(area_sqkm) if area_sqkm else 0
        business_count = int(business_count) if business_count else 0
        density = business_count / area_sqkm if area_sqkm > 0 else 0
        if density > 0:  # Only add to densities for quantile calculation
            densities.append(density)
        
        features.append({
            "type": "Feature",
            "geometry": json.loads(geojson),
            "properties": {
                "county_name": county_name,
                "state": state,
                "business_count": business_count,
                "area_sqkm": round(area_sqkm, 2),
                "density_per_sqkm": round(density, 2)
            }
        })
    
    # Calculate quantiles based on visible counties with density > 0
    if densities:
        densities.sort()
        q1 = round(densities[int(len(densities) * 0.25)], 2)
        q2 = round(densities[int(len(densities) * 0.50)], 2)
        q3 = round(densities[int(len(densities) * 0.75)], 2)
        max_val = round(max(densities), 2)
    else:
        q1 = q2 = q3 = max_val = 0
    
    quantiles = {
        "q1": q1,
        "q2": q2,
        "q3": q3,
        "max": max_val
    }
    
    return {
        "type": "FeatureCollection",
        "features": features,
        "quantiles": quantiles
    }