import psycopg2
import os

DATABASE_URL = os.getenv("DATABASE_URL")

def execute_query(query: str, fetch: bool = False):
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    cur.execute(query)

    result = None
    if fetch:
        result = cur.fetchall()

    conn.commit()
    cur.close()
    conn.close()

    return result