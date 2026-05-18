import json
from tqdm import tqdm
from sqlalchemy import text
from db import engine
from utils import batch_iter

INSERT_BUSINESS = text("""
INSERT INTO businesses (
    business_id, name, address, city, state,
    postal_code, latitude, longitude,
    stars, review_count, is_open, geom
)
VALUES (
    :business_id, :name, :address, :city, :state,
    :postal_code, :latitude, :longitude,
    :stars, :review_count, :is_open,
    ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography
)
ON CONFLICT (business_id) DO NOTHING
""")

INSERT_CATEGORY = text("""
INSERT INTO categories (name)
VALUES (:name)
ON CONFLICT (name) DO NOTHING
""")

INSERT_BUSINESS_CATEGORY = text("""
INSERT INTO business_categories (business_id, category_id)
SELECT :business_id, category_id
FROM categories
WHERE name = :name
ON CONFLICT DO NOTHING
""")


def load_businesses(path):
    print("Loading businesses...")

    business_data = []
    relations = []

    with open(path, "r", encoding="utf-8") as f:
        for line in tqdm(f, desc="Reading businesses"):
            obj = json.loads(line)

            if not obj.get("categories") or "Restaurants" not in obj["categories"]:
                continue

            business_id = obj["business_id"]

            business_data.append({
                "business_id": business_id,
                "name": obj.get("name"),
                "address": obj.get("address"),
                "city": obj.get("city"),
                "state": obj.get("state"),
                "postal_code": obj.get("postal_code"),
                "latitude": obj.get("latitude"),
                "longitude": obj.get("longitude"),
                "stars": obj.get("stars"),
                "review_count": obj.get("review_count"),
                "is_open": bool(obj.get("is_open"))
            })

            # 🔥 категории
            raw = obj.get("categories", "")
            cats = [c.strip() for c in raw.split(",") if c.strip()]

            for c in cats:
                relations.append({
                    "business_id": business_id,
                    "name": c
                })

    with engine.begin() as conn:

        # 1. businesses
        for batch in tqdm(list(batch_iter(business_data, 1000)), desc="Inserting businesses"):
            conn.execute(INSERT_BUSINESS, batch)

        # 2. categories (distinct)
        unique_categories = list({r["name"] for r in relations})

        for batch in tqdm(list(batch_iter(unique_categories, 1000)), desc="Inserting categories"):
            conn.execute(
                INSERT_CATEGORY,
                [{"name": c} for c in batch]
            )

        # 3. business_categories
        for batch in tqdm(list(batch_iter(relations, 2000)), desc="Linking categories"):
            conn.execute(INSERT_BUSINESS_CATEGORY, batch)

    print("Businesses + categories done")