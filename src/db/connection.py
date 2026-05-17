import os
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager


def get_connection_string(pooled: bool = False) -> str:
    url = os.environ["DATABASE_URL"]
    if pooled and "-pooler" not in url:
        # Insert -pooler into the hostname for Streamlit / multi-connection contexts
        url = url.replace(".neon.tech", "-pooler.neon.tech", 1)
    return url


@contextmanager
def get_conn(pooled: bool = False):
    conn = psycopg2.connect(get_connection_string(pooled))
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_cursor(pooled: bool = False):
    with get_conn(pooled) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur


def apply_schema():
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path) as f:
        sql = f.read()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
    print("Schema applied.")
