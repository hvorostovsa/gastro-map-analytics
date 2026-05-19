from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Mapping, cast

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from statsmodels.tsa.statespace.sarimax import SARIMAX

from db import execute_query


router = APIRouter(prefix="/demand", tags=["demand"])

Granularity = Literal["day", "week", "month", "quarter", "year"]


@dataclass(frozen=True)
class _Business:
    business_id: str
    name: str | None
    city: str | None
    state: str | None


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date '{value}', expected YYYY-MM-DD") from e


def _actual_points_for_dates(df_full: pd.DataFrame, dates: list[date]) -> list[Mapping[str, Any]]:
    if df_full.empty or not dates:
        return []
    lookup = df_full.set_index("ds")
    out: list[Mapping[str, Any]] = []
    for d in dates:
        if d in lookup.index:
            row = lookup.loc[d]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            out.append({"date": d.isoformat(), "actual": float(row.get("checkins", 0.0) or 0.0)})
    return out


def _granularity_to_date_trunc(granularity: Granularity) -> str:
    if granularity == "day":
        return "day"
    if granularity == "week":
        return "week"
    if granularity == "month":
        return "month"
    if granularity == "quarter":
        return "quarter"
    if granularity == "year":
        return "year"
    raise HTTPException(status_code=400, detail="Unsupported granularity")


def _granularity_freq(granularity: Granularity) -> str:
    if granularity == "day":
        return "D"
    if granularity == "week":
        return "W-MON"
    if granularity == "month":
        return "MS"
    if granularity == "quarter":
        return "QS"
    if granularity == "year":
        return "YS"
    raise HTTPException(status_code=400, detail="Unsupported granularity")


def _future_dates(*, last_date: date, granularity: Granularity, periods: int) -> list[date]:
    freq = _granularity_freq(granularity)
    idx = pd.date_range(start=pd.Timestamp(last_date), periods=periods + 1, freq=freq)
    return cast(list[date], idx.date.tolist()[1:])


def _season_length(granularity: Granularity) -> int:
    if granularity == "day":
        return 7
    if granularity == "week":
        return 52
    if granularity == "month":
        return 12
    if granularity == "quarter":
        return 4
    return 0


def _series_stats(df: pd.DataFrame) -> Mapping[str, Any]:
    if df.empty:
        return {
            "mean": None,
        }

    y = df["checkins"].astype("float64")
    return {"mean": float(y.mean()) if len(y) > 0 else None}


def _activity_summary(
    df_dense: pd.DataFrame,
    *,
    window: int = 5,
    checkins_near_mean_ratio: float = 0.8,
    min_review_periods: int = 4,
) -> Mapping[str, Any]:
    if df_dense.empty:
        return {
            "last_active_date": None,
            "last_checkin_date": None,
            "last_review_date": None,
        }

    df_dense = df_dense.sort_values("ds")
    checkins = df_dense["checkins"].astype("float64")
    reviews = df_dense["review_count"].astype("int64") if "review_count" in df_dense.columns else pd.Series(0, index=df_dense.index)

    last_checkin_date: str | None = None
    checkin_mask = checkins > 0
    if bool(checkin_mask.any()):
        last_checkin_date = cast(date, df_dense.loc[checkin_mask, "ds"].iloc[-1]).isoformat()

    last_review_date: str | None = None
    review_mask = reviews > 0
    if bool(review_mask.any()):
        last_review_date = cast(date, df_dense.loc[review_mask, "ds"].iloc[-1]).isoformat()

    if len(df_dense) < window:
        return {
            "last_active_date": None,
            "last_checkin_date": last_checkin_date,
            "last_review_date": last_review_date,
        }

    mean_checkins = float(checkins.mean())
    if mean_checkins <= 0.0:
        return {
            "last_active_date": None,
            "last_checkin_date": last_checkin_date,
            "last_review_date": last_review_date,
        }

    threshold = mean_checkins * checkins_near_mean_ratio

    last_active: date | None = None
    for end_idx in range(window - 1, len(df_dense)):
        sl = slice(end_idx - window + 1, end_idx + 1)
        chk = checkins.iloc[sl]
        rev = reviews.iloc[sl]

        checkins_ok = bool((chk >= threshold).all())
        reviews_ok = int((rev > 0).sum()) >= min_review_periods

        if checkins_ok and reviews_ok:
            last_active = cast(date, df_dense["ds"].iloc[end_idx])

    return {
        "last_active_date": last_active.isoformat() if last_active is not None else None,
        "last_checkin_date": last_checkin_date,
        "last_review_date": last_review_date,
    }


def _get_business(business_id: str) -> _Business:
    row = execute_query(
        """
        SELECT business_id, name, city, state
        FROM businesses
        WHERE business_id = %s
        """,
        fetch=True,
        params=(business_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="business_id not found")
    r = cast(tuple[Any, ...], row[0])
    return _Business(
        business_id=cast(str, r[0]),
        name=cast(str | None, r[1]),
        city=cast(str | None, r[2]),
        state=cast(str | None, r[3]),
    )


def _fetch_checkins_series(
    *,
    business_id: str,
    granularity: Granularity,
    start: date | None,
    end: date | None,
) -> pd.DataFrame:
    trunc = _granularity_to_date_trunc(granularity)

    where = ["business_id = %s", "checkin_time IS NOT NULL"]
    params: list[object] = [business_id]

    if start:
        where.append("checkin_time::date >= %s")
        params.append(start)
    if end:
        where.append("checkin_time::date <= %s")
        params.append(end)

    where_sql = " AND ".join(where)
    rows = execute_query(
        f"""
        SELECT date_trunc(%s, checkin_time)::date AS ds,
               COUNT(*)::int AS checkins
        FROM checkins
        WHERE {where_sql}
        GROUP BY ds
        ORDER BY ds
        """,
        fetch=True,
        params=tuple([trunc] + params),
    )

    if not rows:
        return pd.DataFrame(columns=["ds", "checkins"]).astype({"checkins": "int64"})

    df = pd.DataFrame(rows, columns=["ds", "checkins"])
    df["ds"] = pd.to_datetime(df["ds"]).dt.date
    df["checkins"] = df["checkins"].astype("int64")
    return df


def _fetch_reviews_series(
    *,
    business_id: str,
    granularity: Granularity,
    start: date | None,
    end: date | None,
) -> pd.DataFrame:
    trunc = _granularity_to_date_trunc(granularity)

    where = ["business_id = %s", "review_date IS NOT NULL"]
    params: list[object] = [business_id]

    if start:
        where.append("review_date::date >= %s")
        params.append(start)
    if end:
        where.append("review_date::date <= %s")
        params.append(end)

    where_sql = " AND ".join(where)
    rows = execute_query(
        f"""
        SELECT date_trunc(%s, review_date)::date AS ds,
               COUNT(*)::int AS review_count,
               AVG(stars)::float AS avg_review_stars,
               AVG(LENGTH(COALESCE(text, '')) )::float AS avg_review_length
        FROM reviews
        WHERE {where_sql}
        GROUP BY ds
        ORDER BY ds
        """,
        fetch=True,
        params=tuple([trunc] + params),
    )

    if not rows:
        return pd.DataFrame(
            columns=[
                "ds",
                "review_count",
                "avg_review_stars",
                "avg_review_length",
            ]
        )

    df = pd.DataFrame(
        rows,
        columns=[
            "ds",
            "review_count",
            "avg_review_stars",
            "avg_review_length",
        ],
    )
    df["ds"] = pd.to_datetime(df["ds"]).dt.date
    df["review_count"] = df["review_count"].astype("int64")
    return df


def _build_history_frame(
    *,
    business_id: str,
    granularity: Granularity,
    start: date | None,
    end: date | None,
    dense: bool,
) -> pd.DataFrame:
    checkins = _fetch_checkins_series(
        business_id=business_id,
        granularity=granularity,
        start=start,
        end=end,
    )
    reviews = _fetch_reviews_series(
        business_id=business_id,
        granularity=granularity,
        start=start,
        end=end,
    )

    if checkins.empty and reviews.empty:
        return pd.DataFrame(
            columns=[
                "ds",
                "checkins",
                "review_count",
                "avg_review_stars",
                "avg_review_length",
            ]
        )

    all_dates: list[date] = []
    if not checkins.empty:
        all_dates.extend(cast(list[date], checkins["ds"].tolist()))
    if not reviews.empty:
        all_dates.extend(cast(list[date], reviews["ds"].tolist()))

    min_d = min(all_dates)
    max_d = max(all_dates)

    if dense:
        freq = _granularity_freq(granularity)
        idx = pd.date_range(start=pd.Timestamp(min_d), end=pd.Timestamp(max_d), freq=freq)
        base = pd.DataFrame({"ds": idx.date})
        df = base.merge(checkins, on="ds", how="left").merge(reviews, on="ds", how="left")
    else:
        df = pd.merge(checkins, reviews, on="ds", how="outer").sort_values("ds")

    df["checkins"] = df["checkins"].fillna(0).astype("int64")
    df["review_count"] = df["review_count"].fillna(0).astype("int64")
    for col in ["avg_review_stars", "avg_review_length"]:
        if col in df.columns:
            df[col] = df[col].astype("float64")

    return df


def _frame_to_points(df: pd.DataFrame) -> list[Mapping[str, Any]]:
    points: list[Mapping[str, Any]] = []
    if df.empty:
        return points

    for _, row in df.iterrows():
        d = cast(date, row["ds"])
        points.append(
            {
                "date": d.isoformat(),
                "checkins": int(row.get("checkins", 0) or 0),
                "review_count": int(row.get("review_count", 0) or 0),
                "avg_review_stars": float(row["avg_review_stars"]) if pd.notna(row.get("avg_review_stars")) else None,
                "avg_review_length": float(row["avg_review_length"]) if pd.notna(row.get("avg_review_length")) else None,
            }
        )
    return points


def _lag_correlations(df: pd.DataFrame, max_lag: int) -> list[Mapping[str, Any]]:
    if df.empty or len(df) < 3:
        return []

    y = pd.Series(
        np.log1p(df["checkins"].astype("float64").to_numpy(dtype="float64")),
        index=df.index,
    )
    x = pd.Series(
        np.log1p(df["review_count"].astype("float64").to_numpy(dtype="float64")),
        index=df.index,
    )

    out: list[Mapping[str, Any]] = []
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            ya = y.iloc[:lag]
            xa = x.iloc[-lag:]
        elif lag > 0:
            ya = y.iloc[lag:]
            xa = x.iloc[:-lag]
        else:
            ya = y
            xa = x

        if len(ya) < 5 or float(ya.std(ddof=0)) == 0.0 or float(xa.std(ddof=0)) == 0.0:
            corr_val: float | None = None
        else:
            corr = ya.corr(xa)
            corr_val = float(corr) if pd.notna(corr) else None

        out.append({"lag": lag, "corr": corr_val})
    return out


def _build_future_exog(
    df: pd.DataFrame,
    steps: int,
    exog_cols: list[str],
) -> np.ndarray:
    if not exog_cols:
        return np.zeros((steps, 0), dtype="float64")

    recent = df[exog_cols].copy()
    recent = recent.replace([np.inf, -np.inf], np.nan)
    recent = recent.fillna(0.0)

    base = recent.tail(min(7, len(recent))).mean(axis=0).to_frame().T.to_numpy(dtype="float64")

    return np.repeat(base, repeats=steps, axis=0)


def _fit_and_forecast(
    *,
    df: pd.DataFrame,
    granularity: Granularity,
    horizon: int,
) -> tuple[pd.DataFrame, Mapping[str, Any]]:
    if df.empty:
        raise HTTPException(status_code=400, detail="No history found for this business")

    if len(df) < 10:
        raise HTTPException(
            status_code=400,
            detail=f"Not enough history points to fit SARIMAX (need >= 10, got {len(df)}). Try smaller granularity or a longer date range.",
        )

    y = df["checkins"].astype("float64")
    exog_cols = [
        "review_count",
        "avg_review_stars",
        "avg_review_length",
    ]
    exog_cols = [c for c in exog_cols if c in df.columns]

    exog = df[exog_cols].copy() if exog_cols else pd.DataFrame(index=df.index)
    exog = exog.replace([np.inf, -np.inf], np.nan)
    exog = exog.fillna(0.0).astype("float64")

    future_exog = _build_future_exog(df, horizon, exog_cols)

    season = _season_length(granularity)
    use_seasonal = season >= 2 and len(df) >= 2 * season
    seasonal_order = (1, 0, 1, season) if use_seasonal else (0, 0, 0, 0)

    try:
        model = SARIMAX(
            endog=y,
            exog=exog if exog_cols else None,
            order=(1, 1, 1),
            seasonal_order=seasonal_order,
            enforce_stationarity=False,
            enforce_invertibility=False,
        )
        res = model.fit(disp=False)

        fc_res = res.get_forecast(steps=horizon, exog=future_exog if exog_cols else None)
        mean = fc_res.predicted_mean
        ci = fc_res.conf_int(alpha=0.05)

        start_d = cast(date, df["ds"].iloc[-1])
        future_dates = _future_dates(last_date=start_d, granularity=granularity, periods=horizon)

        lower_col = ci.columns[0]
        upper_col = ci.columns[1]
        fc = pd.DataFrame(
            {
                "date": [d.isoformat() for d in future_dates],
                "yhat": np.maximum(0.0, mean.to_numpy(dtype="float64")),
                "yhat_lower": np.maximum(0.0, ci[lower_col].to_numpy(dtype="float64")),
                "yhat_upper": np.maximum(0.0, ci[upper_col].to_numpy(dtype="float64")),
            }
        )

        info = {
            "type": "SARIMAX",
            "order": [1, 1, 1],
            "seasonal_order": list(seasonal_order),
            "features": exog_cols,
            "horizon": horizon,
            "granularity": granularity,
        }
        return fc, info
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"SARIMAX failed to fit/forecast for this series: {e}") from e


@router.get("/history")
def demand_history(
    business_id: str = Query(..., min_length=1),
    granularity: Granularity = Query(default="day"),
    start: str | None = Query(default=None, description="YYYY-MM-DD"),
    end: str | None = Query(default=None, description="YYYY-MM-DD"),
    dense: bool = Query(
        default=False,
        description="If true, returns continuous series with zero-filled gaps; if false, returns only periods where checkins or reviews exist",
    ),
):
    business = _get_business(business_id)
    start_d = _parse_iso_date(start)
    end_d = _parse_iso_date(end)
    if start_d and end_d and start_d > end_d:
        raise HTTPException(status_code=400, detail="start cannot be after end")

    df = _build_history_frame(
        business_id=business_id,
        granularity=granularity,
        start=start_d,
        end=end_d,
        dense=dense,
    )

    points = _frame_to_points(df)
    if df.empty:
        return {
            "business": business.__dict__,
            "granularity": granularity,
            "date_range": None,
            "points": [],
            "relations": {"lag_correlations": []},
        }

    date_range = {
        "start": cast(date, df["ds"].iloc[0]).isoformat(),
        "end": cast(date, df["ds"].iloc[-1]).isoformat(),
    }

    # Useful for choosing train_end: compute activity on a dense, gap-free series.
    df_dense = df if dense else _build_history_frame(
        business_id=business_id,
        granularity=granularity,
        start=start_d,
        end=end_d,
        dense=True,
    )
    activity = _activity_summary(df_dense)

    if granularity in ("month", "quarter", "year"):
        df_corr = _build_history_frame(
            business_id=business_id,
            granularity=granularity,
            start=start_d,
            end=end_d,
            dense=True,
        )
        rel = {"lag_correlations": _lag_correlations(df_corr, 1)}
    else:
        rel = {"lag_correlations": []}

    return {
        "business": business.__dict__,
        "granularity": granularity,
        "date_range": date_range,
        "series_stats": _series_stats(df),
        "activity": activity,
        "points": points,
        "relations": rel,
    }


@router.get("/predict")
def demand_predict(
    business_id: str = Query(..., min_length=1),
    horizon: int = Query(default=14, ge=1, le=90),
    granularity: Granularity = Query(default="day"),
    include_history: bool = Query(default=True),
    train_end: str | None = Query(
        default=None,
        description="Optional backtest cutoff (YYYY-MM-DD). Model is trained only up to this date; forecast is compared to actuals if available.",
    ),
):
    business = _get_business(business_id)

    train_end_d = _parse_iso_date(train_end)

    df = _build_history_frame(
        business_id=business_id,
        granularity=granularity,
        start=None,
        end=train_end_d,
        dense=True,
    )

    if df.empty:
        raise HTTPException(status_code=400, detail="No history found for this business")

    forecast_df, model_info = _fit_and_forecast(
        df=df,
        granularity=granularity,
        horizon=horizon,
    )

    model_info_dict = dict(model_info)
    model_info = {
        "type": model_info_dict.get("type"),
        "features": model_info_dict.get("features", []),
        "horizon": horizon,
        "granularity": granularity,
        "series_stats": _series_stats(df),
    }

    last_hist_date = cast(date, df["ds"].iloc[-1]).isoformat()

    actual_points = None
    if train_end_d is not None:
        df_full = _build_history_frame(
            business_id=business_id,
            granularity=granularity,
            start=None,
            end=None,
            dense=True,
        )
        forecast_dates = [date.fromisoformat(r["date"]) for r in forecast_df.to_dict(orient="records")]
        actual_points = _actual_points_for_dates(df_full, forecast_dates)

    return {
        "business": business.__dict__,
        "granularity": granularity,
        "last_history_date": last_hist_date,
        "model": model_info,
        "history_points": _frame_to_points(df) if include_history else None,
        "forecast_points": forecast_df.to_dict(orient="records"),
        "train_end": train_end_d.isoformat() if train_end_d is not None else None,
        "actual_points": actual_points,
    }
