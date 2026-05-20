from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from db import execute_query
import json

router = APIRouter()


@router.get("/counties/borders")
def get_county_borders(
    state: Optional[str] = Query(None, description="State abbreviation (CA, NV, ID, AZ, LA, FL, MO, TN, IN, PA)"),
    county_name: Optional[str] = Query(None, description="County name (partial match)"),
    simplify: float = Query(0.001, description="Simplification tolerance (0 = no simplification)")
):
    """
    Get county borders as GeoJSON features
    """
    
    sql = """
        SELECT 
            geoid,
            name AS county_name,
            state,
            CASE 
                WHEN %(simplify)s > 0 THEN 
                    ST_AsGeoJSON(ST_Simplify(geom::geometry, %(simplify)s))
                ELSE 
                    ST_AsGeoJSON(geom::geometry)
            END AS geojson,
            ST_Area(geom) / 1000000 AS area_sq_km
        FROM counties
        WHERE state IN ('CA', 'NV', 'ID', 'AZ', 'LA', 'FL', 'MO', 'TN', 'IN', 'PA')
    """
    
    params = {"simplify": simplify}
    
    if state:
        state_upper = state.upper()
        valid_states = ['CA', 'NV', 'ID', 'AZ', 'LA', 'FL', 'MO', 'TN', 'IN', 'PA']
        if state_upper not in valid_states:
            raise HTTPException(status_code=400, detail=f"State must be one of: {', '.join(valid_states)}")
        sql += " AND state = %(state)s"
        params["state"] = state_upper
    
    if county_name:
        sql += " AND name ILIKE %(county_name)s"
        params["county_name"] = f"%{county_name}%"
    
    sql += " ORDER BY state, name"
    
    rows = execute_query(sql, fetch=True, params=params, dict_cursor=True)
    
    if not rows:
        return {
            "type": "FeatureCollection",
            "features": [],
            "total_count": 0
        }
    
    # Format as GeoJSON
    features = []
    for row in rows:
        geojson_data = json.loads(row['geojson']) if row['geojson'] else None
        features.append({
            "type": "Feature",
            "geometry": geojson_data,
            "properties": {
                "geoid": row['geoid'],
                "county_name": row['county_name'],
                "state": row['state'],
                "area_sq_km": round(row['area_sq_km'], 2) if row['area_sq_km'] else None
            }
        })
    
    return {
        "type": "FeatureCollection",
        "features": features,
        "total_count": len(features)
    }


@router.get("/counties/bbox")
def get_counties_bbox(state: Optional[str] = Query(None)):
    """Get overall bounding box for counties"""
    
    sql = """
        SELECT 
            MIN(ST_XMin(geom::geometry)) AS min_lon,
            MIN(ST_YMin(geom::geometry)) AS min_lat,
            MAX(ST_XMax(geom::geometry)) AS max_lon,
            MAX(ST_YMax(geom::geometry)) AS max_lat
        FROM counties
        WHERE state IN ('CA', 'NV', 'ID', 'AZ', 'LA', 'FL', 'MO', 'TN', 'IN', 'PA')
    """
    
    params = {}
    if state:
        state_upper = state.upper()
        sql += " AND state = %(state)s"
        params["state"] = state_upper
    
    rows = execute_query(sql, fetch=True, params=params, dict_cursor=True)
    
    if rows and rows[0]['min_lon'] is not None:
        row = rows[0]
        return {
            "bbox": [row['min_lon'], row['min_lat'], row['max_lon'], row['max_lat']],
            "min_lon": row['min_lon'],
            "min_lat": row['min_lat'],
            "max_lon": row['max_lon'],
            "max_lat": row['max_lat']
        }
    
    raise HTTPException(status_code=404, detail="No counties found")


@router.get("/counties/{geoid}")
def get_county_by_id(geoid: str):
    """Get a single county by GEOID"""
    
    sql = """
        SELECT 
            geoid,
            name AS county_name,
            state,
            ST_AsGeoJSON(geom::geometry) AS geojson,
            ST_Area(geom) / 1000000 AS area_sq_km,
            ST_X(ST_Centroid(geom::geometry)) AS centroid_lon,
            ST_Y(ST_Centroid(geom::geometry)) AS centroid_lat
        FROM counties
        WHERE geoid = %(geoid)s
    """
    
    rows = execute_query(sql, fetch=True, params={"geoid": geoid}, dict_cursor=True)
    
    if not rows:
        raise HTTPException(status_code=404, detail=f"County with geoid {geoid} not found")
    
    row = rows[0]
    geojson_data = json.loads(row['geojson']) if row['geojson'] else None
    
    return {
        "type": "Feature",
        "geometry": geojson_data,
        "properties": {
            "geoid": row['geoid'],
            "county_name": row['county_name'],
            "state": row['state'],
            "area_sq_km": round(row['area_sq_km'], 2) if row['area_sq_km'] else None,
            "centroid": {
                "lon": row['centroid_lon'],
                "lat": row['centroid_lat']
            }
        }
    }