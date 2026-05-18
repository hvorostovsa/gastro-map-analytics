from db import engine
from sqlalchemy import text


def batch_iter(iterable, batch_size):
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def load_valid_business_ids():
    print("Loading valid business_ids from DB...")

    with engine.connect() as conn:
        result = conn.execute(text("SELECT business_id FROM businesses"))
        return set(row[0] for row in result)
