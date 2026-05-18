import json
from sqlalchemy import text
from db import engine
from utils import load_valid_business_ids

INSERT_REVIEW = text("""
INSERT INTO reviews (
    review_id, business_id, user_id,
    stars, useful, funny, cool,
    text, review_date
)
VALUES (
    :review_id, :business_id, :user_id,
    :stars, :useful, :funny, :cool,
    :text, :review_date
)
ON CONFLICT (review_id) DO NOTHING
""")

TOTAL_REVIEWS = 4_724_471

def load_reviews(path):
    print("Loading reviews...")

    valid_business_ids = load_valid_business_ids()
    print(f"Valid businesses: {len(valid_business_ids)}")

    buffer = []
    inserted = 0
    batch_count = 0

    BATCH_SIZE = 1000
    LOG_EVERY = 100

    with open(path, "r", encoding="utf-8") as f:

        for line in f:
            obj = json.loads(line)

            bid = obj.get("business_id")
            if bid not in valid_business_ids:
                continue

            buffer.append({
                "review_id": obj["review_id"],
                "business_id": bid,
                "user_id": obj["user_id"],
                "stars": obj["stars"],
                "useful": obj["useful"],
                "funny": obj["funny"],
                "cool": obj["cool"],
                "text": obj["text"],
                "review_date": obj["date"]
            })

            if len(buffer) >= BATCH_SIZE:

                with engine.begin() as conn:
                    conn.execute(INSERT_REVIEW, buffer)

                inserted += len(buffer)
                buffer.clear()
                batch_count += 1

                if batch_count % LOG_EVERY == 0:

                    percent = (inserted / TOTAL_REVIEWS) * 100

                    print(
                        f"[REVIEWS] "
                        f"batches={batch_count} | "
                        f"inserted={inserted} | "
                        f"{percent:.2f}%"
                    )

        # остаток
        if buffer:
            with engine.begin() as conn:
                conn.execute(INSERT_REVIEW, buffer)

            inserted += len(buffer)
            batch_count += 1

    print(
        f"DONE | batches={batch_count} | "
        f"inserted={inserted} / {TOTAL_REVIEWS}"
    )