import mysql.connector
import config as app_config
from contextlib import contextmanager


def mysql_conn():
    """
    Single canonical MySQL connector.
    """
    return mysql.connector.connect(
        host=app_config.DB_HOST,
        port=app_config.DB_PORT,
        user=app_config.DB_USER,
        password=app_config.DB_PASS,
        database=app_config.DB_NAME,
    )


@contextmanager
def mysql_session(dictionary: bool = False):
    """
    Context manager that yields (conn, cursor) and always closes them.

    Use this for short-lived operations, including from background threads.
    """
    conn = mysql_conn()
    cur = conn.cursor(dictionary=dictionary)
    try:
        yield conn, cur
    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def query(sql: str, params=None, dictionary: bool = True):
    """Run a SELECT and return all rows."""
    with mysql_session(dictionary=dictionary) as (conn, cur):
        cur.execute(sql, params or ())
        return cur.fetchall() or []


def query_one(sql: str, params=None, dictionary: bool = True):
    """Run a SELECT and return a single row (or None)."""
    with mysql_session(dictionary=dictionary) as (conn, cur):
        cur.execute(sql, params or ())
        return cur.fetchone()


def scalar(sql: str, params=None, default=None):
    """Return the first column of the first row (or default)."""
    row = query_one(sql, params=params, dictionary=False)
    if row is None:
        return default
    try:
        return row[0]
    except Exception:
        return default


def exec(sql: str, params=None, many: bool = False):
    """
    Run an INSERT/UPDATE/DELETE and commit automatically.

    Returns: {"rowcount": int|None, "lastrowid": int|None}
    """
    with mysql_session(dictionary=False) as (conn, cur):
        if many:
            cur.executemany(sql, params or ())
        else:
            cur.execute(sql, params or ())
        conn.commit()
        return {
            "rowcount": getattr(cur, "rowcount", None),
            "lastrowid": getattr(cur, "lastrowid", None),
        }
