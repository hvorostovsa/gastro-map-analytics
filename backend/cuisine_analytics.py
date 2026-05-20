from fastapi import APIRouter, Query
from db import execute_query
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics/cuisines", tags=["cuisines"])

CUISINE_DENYLIST = {
    "Restaurants", "Food", "Nightlife", "Bars", "Event Planning & Services",
    "Caterers", "Shopping", "Hotels & Travel", "Active Life", "Arts & Entertainment",
    "Local Services", "Professional Services", "Beauty & Spas", "Health & Medical",
    "Home Services", "Automotive", "Education", "Public Services & Government",
}

TARGET_STATES = ("CA", "NV", "ID", "AZ", "LA", "FL", "MO", "TN", "IN", "PA")


def cuisine_filter_sql(alias: str = "c") -> str:
    deny_placeholders = ", ".join(["%s"] * len(CUISINE_DENYLIST))
    return f"{alias}.name NOT IN ({deny_placeholders})"


def cuisine_filter_params() -> list[str]:
    return sorted(CUISINE_DENYLIST)


def is_denied_cuisine(cuisine: str) -> bool:
    return cuisine in CUISINE_DENYLIST


def parse_bbox(bbox: str) -> tuple[float, float, float, float]:
    south, west, north, east = map(float, bbox.split(","))

    if south > north:
        south, north = north, south
    if west > east:
        west, east = east, west

    return south, west, north, east


@router.get("/distribution")
def cuisine_distribution(limit: int = Query(20, ge=1, le=200)):
    cuisine_filter = cuisine_filter_sql("c")
    sql = """
        SELECT
            c.name,
            COUNT(*) AS business_count
        FROM categories c
        JOIN business_categories bc ON bc.category_id = c.category_id
        JOIN businesses b ON b.business_id = bc.business_id
        WHERE {cuisine_filter}
        GROUP BY c.name
        ORDER BY business_count DESC
        LIMIT %s
    """.format(cuisine_filter=cuisine_filter)

    params = tuple(cuisine_filter_params() + [limit])
    rows = execute_query(sql, fetch=True, params=params)

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
    if is_denied_cuisine(cuisine):
        return []

    state_placeholders = ", ".join(["%s"] * len(TARGET_STATES))
    sql = f"""
        SELECT
            co.geoid,
            co.name AS area_name,
            co.state,
            ST_Y(ST_Centroid(co.geom::geometry)) AS lat,
            ST_X(ST_Centroid(co.geom::geometry)) AS lon,
            COUNT(DISTINCT b.business_id) AS business_count
        FROM businesses b
        JOIN business_categories bc ON bc.business_id = b.business_id
        JOIN categories c ON c.category_id = bc.category_id
        JOIN counties co ON ST_Intersects(
            co.geom,
            ST_SetSRID(ST_MakePoint(b.longitude, b.latitude), 4326)::geography
        )
        WHERE c.name = %s
          AND b.latitude IS NOT NULL
          AND b.longitude IS NOT NULL
          AND co.state IN ({state_placeholders})
        GROUP BY co.geoid, co.name, co.state, co.geom
        ORDER BY business_count DESC
        LIMIT %s
    """

    rows = execute_query(sql, fetch=True, params=(cuisine, *TARGET_STATES, limit))

    return [
        {
            "geoid": r[0],
            "area_name": r[1],
            "state": r[2],
            "lat": float(r[3]) if r[3] is not None else None,
            "lon": float(r[4]) if r[4] is not None else None,
            "business_count": r[5],
        }
        for r in rows or []
    ]


@router.get("/area-top")
def top_cuisine_by_area(
    limit: int = Query(500, ge=1, le=2000),
):
    cuisine_filter = cuisine_filter_sql("c")
    state_placeholders = ", ".join(["%s"] * len(TARGET_STATES))
    sql = f"""
        WITH cuisine_counts AS (
            SELECT
                co.geoid,
                co.name AS area_name,
                co.state,
                ST_Y(ST_Centroid(co.geom::geometry)) AS lat,
                ST_X(ST_Centroid(co.geom::geometry)) AS lon,
                c.name AS cuisine,
                COUNT(DISTINCT b.business_id) AS business_count
            FROM businesses b
            JOIN business_categories bc ON bc.business_id = b.business_id
            JOIN categories c ON c.category_id = bc.category_id
            JOIN counties co ON ST_Intersects(
                co.geom,
                ST_SetSRID(ST_MakePoint(b.longitude, b.latitude), 4326)::geography
            )
            WHERE {cuisine_filter}
              AND b.latitude IS NOT NULL
              AND b.longitude IS NOT NULL
              AND co.state IN ({state_placeholders})
            GROUP BY co.geoid, co.name, co.state, co.geom, c.name
        ),
        ranked AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY geoid
                    ORDER BY business_count DESC, cuisine ASC
                ) AS rn
            FROM cuisine_counts
        )
        SELECT
            geoid,
            area_name,
            state,
            lat,
            lon,
            cuisine,
            business_count
        FROM ranked
        WHERE rn = 1
        ORDER BY business_count DESC
        LIMIT %s
    """

    params = tuple(cuisine_filter_params() + list(TARGET_STATES) + [limit])
    rows = execute_query(sql, fetch=True, params=params)

    return [
        {
            "geoid": r[0],
            "area_name": r[1],
            "state": r[2],
            "lat": float(r[3]) if r[3] is not None else None,
            "lon": float(r[4]) if r[4] is not None else None,
            "cuisine": r[5],
            "business_count": r[6],
        }
        for r in rows or []
    ]


@router.get("/map")
def cuisine_map(
    cuisine: str | None = None,
    bbox: str | None = None,
    limit: int = Query(1000, ge=1, le=50000),
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
        if is_denied_cuisine(cuisine):
            return []
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
