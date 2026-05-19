from fastapi import APIRouter, Query
from db import execute_query
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics/cuisines", tags=["cuisines"])


def parse_bbox(bbox: str) -> tuple[float, float, float, float]:
    south, west, north, east = map(float, bbox.split(","))

    if south > north:
        south, north = north, south
    if west > east:
        west, east = east, west

    return south, west, north, east


@router.get("/distribution")
def cuisine_distribution(limit: int = Query(20, ge=1, le=200)):
    sql = """
        SELECT
            c.name,
            COUNT(*) AS business_count
        FROM categories c
        JOIN business_categories bc ON bc.category_id = c.category_id
        JOIN businesses b ON b.business_id = bc.business_id
        WHERE c.name <> 'Restaurants'
        GROUP BY c.name
        ORDER BY business_count DESC
        LIMIT %s
    """

    rows = execute_query(sql, fetch=True, params=(limit,))

    return [
        {
            "cuisine": r[0],
            "business_count": r[1],
        }
        for r in rows or []
    ]


@router.get("/by-area")
def cuisine_by_area(
    cuisine: str,
    limit: int = Query(250, ge=1, le=2000),
):
    sql = """
        SELECT
            b.city,
            b.state,
            AVG(b.latitude) AS lat,
            AVG(b.longitude) AS lon,
            COUNT(*) AS business_count
        FROM businesses b
        JOIN business_categories bc ON bc.business_id = b.business_id
        JOIN categories c ON c.category_id = bc.category_id
        WHERE c.name = %s
          AND b.latitude IS NOT NULL
          AND b.longitude IS NOT NULL
          AND b.city IS NOT NULL
        GROUP BY b.city, b.state
        ORDER BY business_count DESC
        LIMIT %s
    """

    rows = execute_query(sql, fetch=True, params=(cuisine, limit))

    return [
        {
            "city": r[0],
            "state": r[1],
            "lat": float(r[2]) if r[2] is not None else None,
            "lon": float(r[3]) if r[3] is not None else None,
            "business_count": r[4],
        }
        for r in rows or []
    ]


@router.get("/map")
def cuisine_map(
    cuisine: str | None = None,
    bbox: str | None = None,
    limit: int = Query(1000, ge=1, le=5000),
):
    conditions = ["b.latitude IS NOT NULL", "b.longitude IS NOT NULL"]
    params: list[object] = []

    if bbox:
        south, west, north, east = parse_bbox(bbox)
        conditions.append("b.latitude BETWEEN %s AND %s")
        conditions.append("b.longitude BETWEEN %s AND %s")
        params.extend([south, north, west, east])

    join_clause = ""
    if cuisine:
        join_clause = "JOIN business_categories bc ON bc.business_id = b.business_id JOIN categories c ON c.category_id = bc.category_id"
        conditions.append("c.name = %s")
        params.append(cuisine)

    where_clause = " AND ".join(conditions)

    sql = f"""
        SELECT DISTINCT
            b.business_id,
            b.name,
            b.latitude,
            b.longitude,
            b.city,
            b.state,
            b.stars,
            b.review_count
        FROM businesses b
        {join_clause}
        WHERE {where_clause}
        ORDER BY b.review_count DESC NULLS LAST
        LIMIT %s
    """

    params.append(limit)

    rows = execute_query(sql, fetch=True, params=tuple(params))

    return [
        {
            "business_id": r[0],
            "name": r[1],
            "lat": float(r[2]) if r[2] is not None else None,
            "lon": float(r[3]) if r[3] is not None else None,
            "city": r[4],
            "state": r[5],
            "stars": float(r[6]) if r[6] is not None else None,
            "review_count": r[7],
        }
        for r in rows or []
    ]
