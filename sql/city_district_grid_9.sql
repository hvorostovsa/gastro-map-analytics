TRUNCATE public.city_district_grid_9;

INSERT INTO public.city_district_grid_9 (city, state, district, west, south, east, north, geom)
WITH city_bbox AS (
  SELECT
    city,
    state,
    MIN(longitude) AS west,
    MIN(latitude)  AS south,
    MAX(longitude) AS east,
    MAX(latitude)  AS north
  FROM public.businesses
  WHERE city IS NOT NULL
    AND state IS NOT NULL
    AND longitude IS NOT NULL
    AND latitude IS NOT NULL
  GROUP BY city, state
),
city_bbox_safe AS (
  SELECT
    city,
    state,
    CASE WHEN east = west THEN west - 0.0005 ELSE west END AS west_s,
    CASE WHEN east = west THEN east + 0.0005 ELSE east END AS east_s,
    CASE WHEN north = south THEN south - 0.0005 ELSE south END AS south_s,
    CASE WHEN north = south THEN north + 0.0005 ELSE north END AS north_s
  FROM city_bbox
),
grid AS (
  SELECT
    b.city,
    b.state,
    r, c,
    (b.west_s  + (b.east_s  - b.west_s)  * (c    / 3.0)) AS cell_west,
    (b.west_s  + (b.east_s  - b.west_s)  * ((c+1)/ 3.0)) AS cell_east,
    (b.south_s + (b.north_s - b.south_s) * (r    / 3.0)) AS cell_south,
    (b.south_s + (b.north_s - b.south_s) * ((r+1)/ 3.0)) AS cell_north
  FROM city_bbox_safe b
  CROSS JOIN generate_series(0,2) AS r
  CROSS JOIN generate_series(0,2) AS c
)
SELECT
  city,
  state,
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
  END AS district,
  cell_west  AS west,
  cell_south AS south,
  cell_east  AS east,
  cell_north AS north,
  ST_MakeEnvelope(cell_west, cell_south, cell_east, cell_north, 4326) AS geom
FROM grid;