import json
from tqdm import tqdm
from sqlalchemy import text
from db import engine
from utils import load_valid_business_ids

INSERT_CHECKIN = text("""
INSERT INTO checkins (
    business_id,
    checkin_time
)
VALUES (
    :business_id,
    :checkin_time
)
ON CONFLICT DO NOTHING
""")

BATCH_SIZE = 5000
LOG_EVERY = 100


def count_valid_checkins(path, valid_business_ids):
    print("Counting valid checkins...")

    total = 0

    with open(path, "r", encoding="utf-8") as f:
        for line in tqdm(f, desc="Counting"):
            obj = json.loads(line)

            business_id = obj.get("business_id")
            if business_id not in valid_business_ids:
                continue

            dates = obj.get("date")
            if not dates:
                continue

            total += len(dates.split(","))

    print(f"Valid checkins total: {total}")
    return total


def load_checkins(path):
    print("Loading checkins...")

    valid_business_ids = load_valid_business_ids()

    total_valid = count_valid_checkins(path, valid_business_ids)

    buffer = []
    inserted = 0
    batch_count = 0

    with engine.begin() as conn:

        with open(path, "r", encoding="utf-8") as f:

            for line in tqdm(f, desc="Reading checkins"):
                obj = json.loads(line)

                business_id = obj.get("business_id")

                if business_id not in valid_business_ids:
                    continue

                dates = obj.get("date")
                if not dates:
                    continue

                for dt in dates.split(","):
                    buffer.append({
                        "business_id": business_id,
                        "checkin_time": dt.strip()
                    })

                if len(buffer) >= BATCH_SIZE:
                    conn.execute(INSERT_CHECKIN, buffer)

                    inserted += len(buffer)
                    batch_count += 1
                    buffer.clear()

                    # 🔥 редкий лог
                    if batch_count % LOG_EVERY == 0:
                        percent = (inserted / total_valid) * 100 if total_valid else 0

                        print(
                            f"[CHECKINS] "
                            f"batches={batch_count} | "
                            f"inserted={inserted} | "
                            f"{percent:.2f}%"
                        )

        # остаток
        if buffer:
            conn.execute(INSERT_CHECKIN, buffer)

            inserted += len(buffer)
            batch_count += 1

    print(
        f"CHECKINS DONE | "
        f"batches={batch_count} | "
        f"inserted={inserted}"
    )