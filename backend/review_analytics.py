from __future__ import annotations

from datetime import datetime
from typing import Any, DefaultDict, Literal, Mapping, cast
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Query
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from db import execute_query

router = APIRouter(prefix="/analytics", tags=["analytics"])
_analyzer = SentimentIntensityAnalyzer()


def _month_span(min_dt: datetime | None, max_dt: datetime | None) -> float:
    if not min_dt and not max_dt:
        return 1.0
    if not min_dt or not max_dt:
        return 1.0
    delta = max_dt - min_dt
    months = delta.total_seconds() / (30.0 * 24 * 3600)
    return max(1.0, months)


FeatureName = Literal[
    "avg_sentiment",
    "avg_stars",
    "avg_review_length",
    "reviews_per_month",
    "review_count",
]


def _normalize_ids(ids: list[str] | None) -> list[str]:
    if not ids:
        return []
    cleaned = [s for s in (sid.strip() for sid in ids) if s]
    return list(dict.fromkeys(cleaned))


def _fetch_capped_reviews(
    *,
    business_ids: list[str] | None = None,
    city: str | None = None,
    max_reviews_per_business: int,
    max_total_reviews: int,
) -> list[tuple[Any, ...]]:
    """Fetch reviews with caps (per business and total) using a window function."""

    where: list[str] = []
    params: list[object] = []

    business_ids = _normalize_ids(business_ids)
    if business_ids:
        where.append("r.business_id = ANY(%s)")
        params.append(business_ids)
    if city:
        where.append("b.city = %s")
        params.append(city)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    rows = execute_query(
        f"""
        WITH filtered AS (
            SELECT
                r.review_id,
                r.business_id,
                r.review_date,
                r.stars,
                r.text,
                b.name,
                b.city,
                b.state,
                ROW_NUMBER() OVER (
                    PARTITION BY r.business_id
                ) AS rn
            FROM reviews r
            JOIN businesses b ON b.business_id = r.business_id
            {where_sql}
        )
        SELECT business_id, review_date, stars, text, name, city, state
        FROM filtered
        WHERE rn <= %s
        LIMIT %s
        """,
        fetch=True,
        params=tuple(params + [max_reviews_per_business, max_total_reviews]),
    )

    return cast(list[tuple[Any, ...]], rows or [])


def _pick_business_ids(
    *,
    city: str | None,
    limit_businesses: int,
    min_reviews: int,
) -> list[str]:
    where: list[str] = ["COALESCE(review_count, 0) >= %s"]
    params: list[object] = [min_reviews]
    if city:
        where.append("city = %s")
        params.append(city)
    where_sql = "WHERE " + " AND ".join(where)

    rows = execute_query(
        f"""
        SELECT business_id
        FROM businesses
        {where_sql}
        LIMIT %s
        """,
        fetch=True,
        params=tuple(params + [limit_businesses]),
    )

    return [cast(str, r[0]) for r in (rows or [])]


def _review_feature_value(*, feature: FeatureName, stars: float | None, text: str, sentiment: float) -> float | None:
    if feature == "avg_sentiment":
        return float(sentiment)
    if feature == "avg_stars":
        return float(stars) if stars is not None else None
    if feature == "avg_review_length":
        return float(len(text))
    return None


def _compute_business_metrics_live(
    *,
    business_id: str | None = None,
    city: str | None = None,
    min_reviews: int = 30,
    limit_businesses: int = 5000,
    max_reviews_per_business: int = 300,
    max_total_reviews: int = 300000,
) -> list[Mapping[str, Any]]:
    candidate_ids: list[str] | None
    if business_id:
        candidate_ids = [business_id]
    else:
        candidate_ids = _pick_business_ids(
            city=city,
            limit_businesses=limit_businesses,
            min_reviews=min_reviews,
        )
        if not candidate_ids:
            return []

    rows = _fetch_capped_reviews(
        business_ids=candidate_ids,
        city=None,
        max_reviews_per_business=max_reviews_per_business,
        max_total_reviews=max_total_reviews,
    )

    if not rows:
        return []

    # Aggregate in Python.
    agg: DefaultDict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "review_count": 0,
            "sum_sentiment": 0.0,
            "sum_length": 0,
            "sum_stars": 0.0,
            "min_date": None,
            "max_date": None,
            "business_id": None,
            "name": None,
            "city": None,
            "state": None,
        }
    )

    for b_id, r_date, stars, text, name, b_city, b_state in rows:
        text = text or ""
        sentiment = _analyzer.polarity_scores(text).get("compound", 0.0)

        item = agg[b_id]
        item["business_id"] = b_id
        item["name"] = name
        item["city"] = b_city
        item["state"] = b_state
        item["review_count"] += 1
        item["sum_sentiment"] += float(sentiment)
        item["sum_length"] += int(len(text))
        if stars is not None:
            item["sum_stars"] += float(stars)

        if isinstance(r_date, datetime):
            if item["min_date"] is None or r_date < item["min_date"]:
                item["min_date"] = r_date
            if item["max_date"] is None or r_date > item["max_date"]:
                item["max_date"] = r_date

    items: list[Mapping[str, Any]] = []
    for b_id, item in agg.items():
        rc = int(item["review_count"] or 0)
        if rc < min_reviews:
            continue

        avg_sentiment = float(item["sum_sentiment"]) / rc
        avg_review_length = float(item["sum_length"]) / rc
        avg_stars = float(item["sum_stars"]) / rc if rc else 0.0
        months = _month_span(item["min_date"], item["max_date"])
        reviews_per_month = rc / months

        items.append(
            {
                "business_id": item["business_id"],
                "name": item["name"],
                "city": item["city"],
                "state": item["state"],
                "avg_sentiment": avg_sentiment,
                "avg_review_length": avg_review_length,
                "avg_stars": avg_stars,
                "review_count": rc,
                "reviews_per_month": float(reviews_per_month),
                "latitude": None,
                "longitude": None,
            }
        )

    items.sort(
        key=lambda r: (
            -int(r.get("review_count") or 0),
            str(r.get("business_id") or ""),
        )
    )
    items = items[:limit_businesses]

    if items:
        ids = [cast(str, item.get("business_id")) for item in items if item.get("business_id")]
        if ids:
            rows = execute_query(
                """
                SELECT business_id, latitude, longitude
                FROM businesses
                WHERE business_id = ANY(%s)
                """,
                fetch=True,
                params=(ids,),
            )
            coords = {cast(str, r[0]): (r[1], r[2]) for r in (rows or [])}
            for item in items:
                bid = cast(str, item.get("business_id") or "")
                if bid in coords:
                    lat, lon = coords[bid]
                    item["latitude"] = float(lat) if lat is not None else None
                    item["longitude"] = float(lon) if lon is not None else None

    return items


@router.get("/business-metrics")
def business_metrics(
    city: str | None = Query(default=None),
    min_reviews: int = Query(default=30, ge=1, le=100000),
    limit: int = Query(default=5000, ge=1, le=50000),
    max_reviews_per_business: int = Query(default=300, ge=1, le=5000, description="live-mode cap"),
    max_total_reviews: int = Query(default=300000, ge=1, le=2000000, description="live-mode cap"),
):
    """Return per-business aggregates needed for plots and correlation."""

    if min_reviews > max_reviews_per_business:
        raise HTTPException(
            status_code=400,
            detail="min_reviews cannot be greater than max_reviews_per_business (otherwise no business can pass the filter)",
        )

    items = _compute_business_metrics_live(
        city=city,
        min_reviews=min_reviews,
        limit_businesses=limit,
        max_reviews_per_business=max_reviews_per_business,
        max_total_reviews=max_total_reviews,
    )
    return {"items": items}


@router.get("/business/{business_id}/summary")
def business_summary(
    business_id: str,
    max_reviews: int = Query(default=2000, ge=1, le=200000, description="Cap reviews processed for this business"),
):
    items = _compute_business_metrics_live(
        business_id=business_id,
        min_reviews=1,
        limit_businesses=1,
        max_reviews_per_business=max_reviews,
        max_total_reviews=max_reviews,
    )

    if not items:
        raise HTTPException(status_code=404, detail="Business not found or no reviews")

    return items[0]


@router.get("/business/{business_id}/location")
def business_location(business_id: str):
    rows = execute_query(
        """
        SELECT business_id, name, city, state, latitude, longitude, stars, review_count
        FROM businesses
        WHERE business_id = %s
        """,
        fetch=True,
        params=(business_id,),
    )

    if not rows:
        raise HTTPException(status_code=404, detail="Business not found")

    r = rows[0]
    return {
        "business_id": r[0],
        "name": r[1],
        "city": r[2],
        "state": r[3],
        "latitude": float(r[4]) if r[4] is not None else None,
        "longitude": float(r[5]) if r[5] is not None else None,
        "stars": float(r[6]) if r[6] is not None else None,
        "review_count": r[7],
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_x2 = sum(x * x for x in xs)
    sum_y2 = sum(y * y for y in ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))

    num = n * sum_xy - sum_x * sum_y
    den_left = n * sum_x2 - sum_x * sum_x
    den_right = n * sum_y2 - sum_y * sum_y
    if den_left <= 0 or den_right <= 0:
        return None
    return num / ((den_left * den_right) ** 0.5)


@router.get("/correlation")
def correlation(
    x: FeatureName = Query(default="avg_sentiment"),
    y: FeatureName = Query(default="avg_stars"),
    business_id: list[str] | None = Query(
        default=None,
        description="Business id(s). Repeat parameter: business_id=...&business_id=...",
    ),
    city: str | None = Query(default=None),
    limit_businesses: int | None = Query(
        default=None,
        ge=1,
        le=5000,
        description="If business_id is not provided, select this many businesses (optionally scoped by city)",
    ),
    min_reviews: int = Query(default=30, ge=1, le=100000),
    max_reviews_per_business: int = Query(default=300, ge=1, le=5000, description="live-mode cap"),
    max_total_reviews: int = Query(default=300000, ge=1, le=2000000, description="live-mode cap"),
):
    if min_reviews > max_reviews_per_business:
        raise HTTPException(
            status_code=400,
            detail="min_reviews cannot be greater than max_reviews_per_business (otherwise no business can pass the filter)",
        )

    if x in {"review_count", "reviews_per_month"} or y in {"review_count", "reviews_per_month"}:
        raise HTTPException(
            status_code=400,
            detail="x/y must be one of: avg_sentiment, avg_stars, avg_review_length (per-review features)",
        )

    selected_ids = _normalize_ids(business_id)

    if not selected_ids and limit_businesses is not None:
        selected_ids = _pick_business_ids(
            city=city,
            limit_businesses=limit_businesses,
            min_reviews=min_reviews,
        )

    if not selected_ids and not city and limit_businesses is None:
        raise HTTPException(
            status_code=400,
            detail="Provide city, business_id(s), or limit_businesses to scope correlation",
        )

    rows = _fetch_capped_reviews(
        business_ids=selected_ids or None,
        city=None if selected_ids else city,
        max_reviews_per_business=max_reviews_per_business,
        max_total_reviews=max_total_reviews,
    )

    if not rows:
        return {
            "x": x,
            "y": y,
            "items": [],
            "caps": {
                "max_reviews_per_business": max_reviews_per_business,
                "max_total_reviews": max_total_reviews,
            },
        }

    per_business: DefaultDict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "name": None,
            "city": None,
            "state": None,
            "xs": [],
            "ys": [],
        }
    )

    need_sentiment = (x == "avg_sentiment") or (y == "avg_sentiment")

    for b_id, _r_date, stars, text, name, b_city, b_state in rows:
        text = text or ""
        sentiment = (
            _analyzer.polarity_scores(text).get("compound", 0.0)
            if need_sentiment
            else 0.0
        )

        xv = _review_feature_value(feature=x, stars=stars, text=text, sentiment=float(sentiment))
        yv = _review_feature_value(feature=y, stars=stars, text=text, sentiment=float(sentiment))
        if xv is None or yv is None:
            continue

        item = per_business[b_id]
        item["name"] = name
        item["city"] = b_city
        item["state"] = b_state
        item["xs"].append(float(xv))
        item["ys"].append(float(yv))

    out_items: list[Mapping[str, Any]] = []
    for b_id, item in per_business.items():
        xs = item["xs"]
        ys = item["ys"]
        n = len(xs)
        if n < min_reviews:
            continue
        r = _pearson(xs, ys)
        out_items.append(
            {
                "business_id": b_id,
                "name": item.get("name"),
                "city": item.get("city"),
                "state": item.get("state"),
                "n": n,
                "pearson_r": r,
            }
        )

    out_items.sort(
        key=lambda it: (
            -int(it.get("n") or 0),
            str(it.get("business_id") or ""),
        )
    )
    if limit_businesses is not None:
        out_items = out_items[:limit_businesses]
    return {
        "x": x,
        "y": y,
        "items": out_items,
        "caps": {
            "max_reviews_per_business": max_reviews_per_business,
            "max_total_reviews": max_total_reviews,
        },
    }
