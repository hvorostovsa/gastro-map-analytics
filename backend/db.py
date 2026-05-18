import os

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")


def execute_query(
    query: str,
    fetch: bool = False,
    params: tuple | dict | None = None,
    dict_cursor: bool = False,
):
    conn = psycopg2.connect(DATABASE_URL)
    try:
        cursor_factory = RealDictCursor if dict_cursor else None
        cur = conn.cursor(cursor_factory=cursor_factory)
        try:
            cur.execute(query, params)

            result = None
            if fetch:
                result = cur.fetchall()

            conn.commit()
            return result
        finally:
            cur.close()
    finally:
        conn.close()