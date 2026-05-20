from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import text

from db import engine


# Cities the user called out as "most frequent".
# Normalization below will unify variants like "Saint" vs "St.".
PRIORITY_CITIES = {
    # "philadelphia",
    # "tampa",
    # "indianapolis",
    # "nashville",
    # "tucson",
    # "new orleans",
    # "edmonton",
    # "saint louis",
    # "reno",
    # "boise",
    # "santa barbara",
    # "clearwater",
    # "wilmington",
    # "metairie",
    # "saint petersburg",
    # "franklin",
    # "sparks",
    # "brandon",
    # "meridian",
    # "largo",
    # "cherry hill",
    # "carmel",
    # "west chester",
    # "kenner",
    # "new port richey",
    # "goleta",
    # "greenwood",
    # "palm harbor",
}


def _norm_city(name: str) -> str:
    s = (name or "").strip().casefold()
    s = s.replace(".", "").replace(",", "")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\bsaint\b", "st", s)
    return s


PRIORITY_CITIES_NORM = {_norm_city(c) for c in PRIORITY_CITIES}


def _sanitize_ident(ident: str) -> str:
    s = (ident or "").strip()
    if not s:
        raise ValueError("district column cannot be empty")
    if not all(ch.isalnum() or ch == "_" for ch in s):
        raise ValueError(f"invalid identifier: {ident}")
    return s


def _pick_feature_name(props: dict[str, Any]) -> str | None:
    for key in (
        "name",
        "NAME",
        "district",
        "DISTRICT",
        "district_name",
        "DISTRICT_NAME",
        "ward",
        "WARD",
        "id",
        "ID",
    ):
        v = props.get(key)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _find_geojson(geo_dir: Path, *, city_norm: str, state: str) -> Path | None:
    geo_dir = geo_dir.resolve()
    if not geo_dir.exists():
        return None

    token = city_norm.replace(" ", "_")
    st = (state or "").strip().casefold()

    preferred = [
        geo_dir / f"{token}_{st}.geojson",
        geo_dir / f"{token}_{st.upper()}.geojson",
        geo_dir / f"{token}.geojson",
        geo_dir / f"{city_norm}.geojson",
    ]
    for p in preferred:
        if p.exists() and p.is_file():
            return p

    candidates: list[Path] = []
    for p in geo_dir.glob("*.geojson"):
        stem = _norm_city(p.stem).replace(" ", "_")
        if token in stem:
            candidates.append(p)

    if not candidates:
        return None

    candidates.sort(key=lambda x: x.name)
    return candidates[0]


def _load_geojson_polygons(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    feats = data.get("features") if isinstance(data, dict) else None
    if not isinstance(feats, list) or not feats:
        raise ValueError(f"GeoJSON must be a FeatureCollection with features: {path}")

    out: list[dict[str, Any]] = []
    for f in feats:
        if not isinstance(f, dict):
            continue
        geom = f.get("geometry")
        if not isinstance(geom, dict):
            continue
        gtype = str(geom.get("type") or "")
        if gtype not in {"Polygon", "MultiPolygon"}:
            continue
        props = f.get("properties") or {}
        if not isinstance(props, dict):
            props = {}

        name = _pick_feature_name(props) or f"district_{len(out) + 1}"
        out.append({"name": name, "geom": json.dumps(geom, ensure_ascii=False)})

    if not out:
        raise ValueError(f"No Polygon/MultiPolygon features found: {path}")
    return out


def _fetch_city_counts() -> list[tuple[str, str, int]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT city, state, COUNT(*) AS n
                FROM businesses
                WHERE city IS NOT NULL AND state IS NOT NULL
                GROUP BY city, state
                ORDER BY n DESC
                """
            )
        )
        return [(str(r[0]), str(r[1]), int(r[2])) for r in rows]


def _assign_from_geojson(
    *,
    geojson_path: Path,
    city_variants: list[str],
    state: str,
    district_column: str,
    overwrite: bool,
) -> None:
    district_column = _sanitize_ident(district_column)
    polygons = _load_geojson_polygons(geojson_path)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TEMP TABLE IF NOT EXISTS tmp_districts (
                    district_name TEXT NOT NULL,
                    geom GEOMETRY(MULTIPOLYGON, 4326) NOT NULL
                ) ON COMMIT DROP;
                """
            )
        )
        conn.execute(text("TRUNCATE tmp_districts;"))
        conn.execute(
            text(
                """
                INSERT INTO tmp_districts (district_name, geom)
                VALUES (
                    :district_name,
                    ST_Multi(
                        ST_SetSRID(
                            ST_MakeValid(ST_GeomFromGeoJSON(:geom_geojson)),
                            4326
                        )
                    )
                );
                """
            ),
            [{"district_name": p["name"], "geom_geojson": p["geom"]} for p in polygons],
        )

        conn.execute(
            text(
                f"""
                UPDATE businesses b
                SET {district_column} = m.district_name
                FROM (
                    SELECT
                        b2.business_id,
                        (
                            SELECT td.district_name
                            FROM tmp_districts td
                            WHERE td.geom && (b2.geom::geometry)
                              AND ST_Contains(td.geom, b2.geom::geometry)
                            ORDER BY ST_Area(td.geom::geography) ASC
                            LIMIT 1
                        ) AS district_name
                    FROM businesses b2
                    WHERE b2.state = :state
                      AND b2.city = ANY(:cities)
                      AND b2.geom IS NOT NULL
                      AND (:overwrite OR b2.{district_column} IS NULL)
                ) m
                WHERE b.business_id = m.business_id
                  AND m.district_name IS NOT NULL
                """
            ),
            {"state": state, "cities": city_variants, "overwrite": overwrite},
        )


def _assign_grid_9(
    *,
    city_variants: list[str],
    state: str,
    district_column: str,
    overwrite: bool,
) -> None:
    """Fallback: always split city bbox into a 3x3 grid (9 districts).

    This guarantees stable assignment and lets you render all 9 districts even
    if some have zero businesses.

    Labels: N, NE, E, SE, S, SW, W, NW, CENTER.
    """

    district_column = _sanitize_ident(district_column)

    with engine.begin() as conn:
        conn.execute(
            text(
                f"""
                WITH bbox AS (
                    SELECT
                        MIN(latitude) AS south,
                        MIN(longitude) AS west,
                        MAX(latitude) AS north,
                        MAX(longitude) AS east
                    FROM businesses
                    WHERE state = :state
                      AND city = ANY(:cities)
                      AND latitude IS NOT NULL
                      AND longitude IS NOT NULL
                ),
                params AS (
                    SELECT
                        south, west, north, east,
                        NULLIF(north - south, 0) AS dlat,
                        NULLIF(east - west, 0) AS dlon
                    FROM bbox
                ),
                calc AS (
                    SELECT
                        b.business_id,
                        CASE
                            WHEN p.dlat IS NULL OR p.dlon IS NULL THEN 'CENTER'
                            ELSE (
                                CASE
                                    WHEN r = 1 AND c = 1 THEN 'CENTER'
                                    WHEN r = 2 AND c = 1 THEN 'N'
                                    WHEN r = 2 AND c = 2 THEN 'NE'
                                    WHEN r = 1 AND c = 2 THEN 'E'
                                    WHEN r = 0 AND c = 2 THEN 'SE'
                                    WHEN r = 0 AND c = 1 THEN 'S'
                                    WHEN r = 0 AND c = 0 THEN 'SW'
                                    WHEN r = 1 AND c = 0 THEN 'W'
                                    WHEN r = 2 AND c = 0 THEN 'NW'
                                    ELSE 'CENTER'
                                END
                            )
                        END AS district_name
                    FROM businesses b
                    CROSS JOIN params p
                    CROSS JOIN LATERAL (
                        SELECT
                            LEAST(2, GREATEST(0, FLOOR((b.latitude - p.south) / p.dlat * 3)))::int AS r,
                            LEAST(2, GREATEST(0, FLOOR((b.longitude - p.west) / p.dlon * 3)))::int AS c
                    ) rc
                    WHERE b.state = :state
                      AND b.city = ANY(:cities)
                      AND b.latitude IS NOT NULL
                      AND b.longitude IS NOT NULL
                      AND (:overwrite OR b.{district_column} IS NULL)
                )
                UPDATE businesses b
                SET {district_column} = c.district_name
                FROM calc c
                WHERE b.business_id = c.business_id
                """
            ),
            {"state": state, "cities": city_variants, "overwrite": overwrite},
        )


def load_districts(
    *,
    geo_dir: str | Path = Path("data") / "geo",
    district_column: str = "district",
    overwrite: bool = False,
    only_priority: bool = False,
) -> None:
    """Assign districts to businesses.

    - For priority cities: if a matching GeoJSON exists under geo_dir, assign by polygons.
    - Otherwise (or if no GeoJSON found): assign 9 pseudo-districts (3x3 grid) inside city bbox.

    City variants like "Saint Louis" and "St. Louis" are grouped together.
    """

    geo_dir = Path(geo_dir)

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for city, state, n in _fetch_city_counts():
        norm = _norm_city(city)
        key = (norm, state)
        if key not in grouped:
            grouped[key] = {"cities": set(), "n": 0}
        grouped[key]["cities"].add(city)
        grouped[key]["n"] += n

    ordered = sorted(grouped.items(), key=lambda kv: -int(kv[1]["n"]))

    print("=== DISTRICTS ASSIGN START ===")
    print(f"geo_dir={geo_dir} | column={district_column} | overwrite={overwrite} | only_priority={only_priority}")

    for (city_norm, state), meta in ordered:
        city_variants = sorted(meta["cities"])
        is_priority = city_norm in PRIORITY_CITIES_NORM

        if only_priority and not is_priority:
            continue

        geojson_path = _find_geojson(geo_dir, city_norm=city_norm, state=state)
        if is_priority and geojson_path is not None:
            try:
                _assign_from_geojson(
                    geojson_path=geojson_path,
                    city_variants=city_variants,
                    state=state,
                    district_column=district_column,
                    overwrite=overwrite,
                )
                print(f"[geojson] ok: {city_variants} ({state}) -> {geojson_path.name}")
                continue
            except Exception as e:
                print(f"[geojson] failed for {city_variants} ({state}): {e}. Falling back to grid.")

        _assign_grid_9(
            city_variants=city_variants,
            state=state,
            district_column=district_column,
            overwrite=overwrite,
        )

    print("=== DONE ===")