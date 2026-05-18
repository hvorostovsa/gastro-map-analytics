CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE businesses (
    business_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,

    address TEXT,
    city TEXT,
    state TEXT,
    postal_code TEXT,

    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,

    stars NUMERIC(2,1),
    review_count INTEGER,

    is_open BOOLEAN,

    geom GEOGRAPHY(POINT, 4326)
);

CREATE TABLE categories (
    category_id SERIAL PRIMARY KEY,
    name TEXT UNIQUE
);

CREATE TABLE business_categories (
    business_id TEXT REFERENCES businesses(business_id),
    category_id INTEGER REFERENCES categories(category_id),

    PRIMARY KEY (business_id, category_id)
);

CREATE TABLE checkins (
    id BIGSERIAL PRIMARY KEY,

    business_id TEXT REFERENCES businesses(business_id),

    checkin_time TIMESTAMP
);

CREATE TABLE reviews (
    review_id TEXT PRIMARY KEY,

    business_id TEXT REFERENCES businesses(business_id),

    user_id TEXT,

    stars INTEGER,

    useful INTEGER,
    funny INTEGER,
    cool INTEGER,

    text TEXT,

    review_date TIMESTAMP
);

CREATE INDEX idx_business_geom
ON businesses
USING GIST (geom);

CREATE INDEX idx_reviews_business
ON reviews(business_id);

CREATE INDEX idx_reviews_date
ON reviews(review_date);

CREATE INDEX idx_checkins_business
ON checkins(business_id);

CREATE INDEX idx_checkins_time
ON checkins(checkin_time);

CREATE INDEX IF NOT EXISTS idx_businesses_city_business
ON businesses (city, business_id);

CREATE INDEX IF NOT EXISTS idx_reviews_business_date_id
ON reviews (business_id, review_date DESC, review_id);