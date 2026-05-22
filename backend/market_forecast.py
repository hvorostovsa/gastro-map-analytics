from __future__ import annotations

from datetime import datetime
from math import log1p
from typing import Any, DefaultDict, Literal, Mapping, cast
from collections import defaultdict

from fastapi import APIRouter, Query

from db import execute_query


router = APIRouter(prefix="/api/market", tags=["market"])

ForecastMethod = Literal["linear_all", "weighted"]


def _split_csv(values: list[str] | None) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    for v in values:
        if v is None:
            continue
        parts = [p.strip() for p in str(v).split(",")]
        for p in parts:
            if not p:
                continue
            if p.lower() == "all":
                continue
            if p:
                out.append(p)
    # preserve order, unique
    return list(dict.fromkeys(out))


def _linear_forecast_by_year(
    history: Mapping[int, int],
    *,
    years_ahead: int,
    current_year: int,
    method: str = "linear_all",
) -> tuple[dict[int, int], int]:
    """Forecast restaurant openings.
    
    Methods:
    - linear_all: Linear regression over all data
    - weighted: Weighted least squares (recent years have higher weight)
    """
    if years_ahead <= 0:
        return {}, 0

    xs = sorted(history.keys())
    ys = [int(history[x]) for x in xs]
    if not xs:
        forecast = {current_year + i: 0 for i in range(1, years_ahead + 1)}
        return forecast, 0

    if len(xs) == 1:
        mean_y = float(ys[0])
        forecast = {
            current_year + i: max(0, int(round(mean_y)))
            for i in range(1, years_ahead + 1)
        }
        return forecast, int(sum(forecast.values()))

    n = float(len(xs))
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    if method == "weighted":
        # Weight recent years higher: weight = 1 + (year_index / total_years)
        weights = [1.0 + float(i) / n for i in range(len(xs))]
        sum_w = sum(weights)
        mean_x = sum(w * x for w, x in zip(weights, xs)) / sum_w
        mean_y = sum(w * y for w, y in zip(weights, ys)) / sum_w
        sxx = sum(w * (x - mean_x) ** 2 for w, x in zip(weights, xs))
        slope = sum(w * (x - mean_x) * (y - mean_y) for w, x, y in zip(weights, xs, ys)) / sxx if sxx > 0 else 0.0
    else:
        sxx = sum((x - mean_x) ** 2 for x in xs)
        if sxx <= 0:
            slope = 0.0
        else:
            slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / sxx
    
    intercept = mean_y - slope * mean_x

    forecast: dict[int, int] = {}
    for i in range(1, years_ahead + 1):
        yr = current_year + i
        y_hat = intercept + slope * yr
        forecast[yr] = max(0, int(round(float(y_hat))))

    return forecast, int(sum(forecast.values()))


def _scale_01(values: list[float]) -> list[float]:
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    if hi <= lo:
        return [0.0 for _ in values]
    span = hi - lo
    return [(value - lo) / span for value in values]


def _fetch_county_market_rows(
    *,
    states: list[str] | None,
    geoids: list[str] | None,
    lookback_years: int,
    random_n: int | None,
    as_of_year: int | None = None,
) -> list[tuple[Any, ...]]:
    where: list[str] = []
    params: list[object] = []

    states = _split_csv(states)
    geoids = _split_csv(geoids)

    if states:
        where.append("state = ANY(%s)")
        params.append(states)
    if geoids:
        where.append("geoid = ANY(%s)")
        params.append(geoids)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    order_sql = "ORDER BY random()" if random_n is not None else "ORDER BY state, name"
    limit_sql = "LIMIT %s" if random_n is not None else ""
    if random_n is not None:
        params.append(int(random_n))

    # Use as_of_year for filtering window, or current year if not provided
    base_year = int(as_of_year) if as_of_year is not None else int(datetime.utcnow().year)
    params.append(base_year)  # for the openings_by_year CTE filter
    params.append(int(lookback_years))

    sql = f"""
    WITH selected_counties AS (
        SELECT
            geoid,
            name,
            state,
            geom,
            (ST_Area(geom) / 1000000.0)::float AS area_km2
        FROM counties
        {where_sql}
        {order_sql}
        {limit_sql}
    ),
    first_dates AS (
        SELECT business_id, MIN(activity_date) AS first_activity
        FROM (
            SELECT business_id, checkin_time AS activity_date
            FROM checkins
            WHERE checkin_time IS NOT NULL

            UNION ALL

            SELECT business_id, review_date AS activity_date
            FROM reviews
            WHERE review_date IS NOT NULL
        ) a
        GROUP BY business_id
    ),
    open_restaurants_by_county AS (
        SELECT sc.geoid, COUNT(*)::int AS open_restaurants
        FROM selected_counties sc
        JOIN businesses b
          ON b.is_open = TRUE
         AND b.geom IS NOT NULL
         AND ST_Covers(sc.geom::geometry, b.geom::geometry)
        GROUP BY sc.geoid
    ),
    openings_by_year AS (
        SELECT
            sc.geoid,
            EXTRACT(YEAR FROM fd.first_activity)::int AS year,
            COUNT(*)::int AS opened_count
        FROM selected_counties sc
        JOIN businesses b
          ON b.is_open = TRUE
         AND b.geom IS NOT NULL
         AND ST_Covers(sc.geom::geometry, b.geom::geometry)
        JOIN first_dates fd
          ON fd.business_id = b.business_id
        WHERE EXTRACT(YEAR FROM fd.first_activity)::int >= (%s - %s + 1)
        GROUP BY sc.geoid, year
    )
    SELECT
        sc.geoid,
        sc.name,
        sc.state,
        sc.area_km2,
        COALESCE(orc.open_restaurants, 0) AS open_restaurants,
        oby.year,
        COALESCE(oby.opened_count, 0) AS opened_count
    FROM selected_counties sc
    LEFT JOIN open_restaurants_by_county orc
      ON orc.geoid = sc.geoid
    LEFT JOIN openings_by_year oby
      ON oby.geoid = sc.geoid
    ORDER BY sc.state, sc.name, oby.year
    """

    rows = execute_query(sql, fetch=True, params=tuple(params))
    return cast(list[tuple[Any, ...]], rows or [])


def _compute_county_market(
    *,
    states: list[str] | None,
    geoids: list[str] | None,
    years_ahead: int,
    lookback_years: int,
    random_n: int | None,
    as_of_year: int | None = None,
    forecast_method: ForecastMethod = "linear_all",
) -> list[dict[str, Any]]:
    current_year = int(as_of_year) if as_of_year is not None else int(datetime.utcnow().year)
    start_year = current_year - max(1, int(lookback_years)) + 1

    rows = _fetch_county_market_rows(
        states=states,
        geoids=geoids,
        lookback_years=lookback_years,
        random_n=random_n,
        as_of_year=as_of_year,
    )

    agg: DefaultDict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "geoid": None,
            "county": None,
            "state": None,
            "area_km2": None,
            "open_restaurants": 0,
            "history_opened_by_year": {},
        }
    )

    for geoid, name, state, area_km2, open_restaurants, year, opened_count in rows:
        item = agg[str(geoid)]
        item["geoid"] = str(geoid)
        item["county"] = name
        item["state"] = state
        item["area_km2"] = float(area_km2) if area_km2 is not None else None
        item["open_restaurants"] = int(open_restaurants or 0)
        if year is not None:
            item["history_opened_by_year"][int(year)] = int(opened_count or 0)

    out: list[dict[str, Any]] = []
    for item in agg.values():
        history: dict[int, int] = dict(item["history_opened_by_year"] or {})
        for y in range(start_year, current_year + 1):
            history.setdefault(y, 0)
        
        # Filter history to respect lookback_years window
        history = {y: v for y, v in history.items() if start_year <= y <= current_year}
        history = dict(sorted(history.items()))

        forecast_by_year, forecast_total = _linear_forecast_by_year(
            history,
            years_ahead=int(years_ahead),
            current_year=current_year,
            method=forecast_method,
        )

        area_km2 = float(item.get("area_km2") or 0.0)
        open_restaurants = int(item.get("open_restaurants") or 0)

        # Если в округе нет ни одного бизнеса — отбрасываем его из вывода
        if open_restaurants == 0:
            continue
        density = (open_restaurants / area_km2) if area_km2 > 0 else None

        out.append(
            {
                "geoid": item.get("geoid"),
                "county": item.get("county"),
                "state": item.get("state"),
                "area_km2": float(area_km2) if area_km2 > 0 else None,
                "open_restaurants": open_restaurants,
                "density_per_km2": float(density) if density is not None else None,
                "history_opened_by_year": history,
                "forecast_opened_by_year": forecast_by_year,
                "forecast_total_opened_next_years": int(forecast_total),
            }
        )

    if out:
        years = float(max(1, years_ahead))
        activity_scaled = _scale_01([log1p(float(r["open_restaurants"]) or 0.0) for r in out])
        density_scaled = _scale_01([float(r["density_per_km2"] or 0.0) for r in out])
        growth_scaled = _scale_01([float(r["forecast_total_opened_next_years"]) / years for r in out])

        for idx, row in enumerate(out):
            activity = activity_scaled[idx]
            density = density_scaled[idx]
            growth = growth_scaled[idx]
            open_restaurants = float(row["open_restaurants"] or 0.0)

            low_activity_penalty = (1.0 - activity) ** 1.8
            density_penalty = density ** 1.7
            growth_penalty = growth ** 1.4
            low_count_penalty = (1.0 / (1.0 + (open_restaurants / 75.0) ** 1.6))

            score = 100.0 * (
                0.55 * activity
                + 0.25 * (1.0 - density)
                + 0.20 * (1.0 - growth)
                - 0.18 * low_activity_penalty
                - 0.22 * density_penalty
                - 0.10 * growth_penalty
                - 0.22 * low_count_penalty
            )

            row["recommendation_score"] = float(max(0.0, min(100.0, score)))
    else:
        for row in out:
            row["recommendation_score"] = 0.0

    out.sort(key=lambda r: (str(r.get("state") or ""), str(r.get("county") or "")))
    return out


@router.get("/forecast")
def market_forecast(
    years_ahead: int = Query(5, ge=1, le=50),
    lookback_years: int = Query(10, ge=1, le=50),
    states: list[str] | None = Query(None),
    geoids: list[str] | None = Query(None),
    random_n: int | None = Query(None, ge=1, le=5000),
    as_of_year: int = Query(2021, ge=1900, le=2021),
    forecast_method: ForecastMethod = Query("linear_all", pattern="^(linear_all|weighted)$"),
):
    state_list = _split_csv(states)
    geoid_list = _split_csv(geoids)
    return _compute_county_market(
        states=state_list or None,
        geoids=geoid_list or None,
        years_ahead=years_ahead,
        lookback_years=lookback_years,
        random_n=random_n,
        as_of_year=as_of_year,
        forecast_method=forecast_method,
    )
