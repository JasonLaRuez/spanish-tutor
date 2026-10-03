"""Progress and history, read from the word bank with the hand-written SQL in sql/queries/.

Every function takes a connection and returns plain rows (dicts), so the same numbers
serve the web UI, notebooks and tests.
"""

import sqlite3
from functools import cache

from spanish_tutor.config import SQL_DIR

QUERIES = SQL_DIR / "queries"


@cache
def query(name: str) -> str:
    return (QUERIES / f"{name}.sql").read_text(encoding="utf-8")


def rows(conn: sqlite3.Connection, name: str, **params: object) -> list[dict]:
    cursor = conn.execute(query(name), params)
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor]


def words_by_mode(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {"recognition": 0, "production": 0}
    counts.update({r["mode"]: r["words"] for r in rows(conn, "progress_by_mode")})
    return counts


def coverage_by_band(conn: sqlite3.Connection) -> list[dict]:
    return rows(conn, "progress_by_band")


def growth_by_session(conn: sqlite3.Connection) -> list[dict]:
    return rows(conn, "progress_by_session")


def try_using(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Recognized words never used yet, most frequent first."""
    return rows(conn, "progress_gap", limit=limit)


def sessions(conn: sqlite3.Connection) -> list[dict]:
    return rows(conn, "sessions_summary")


def transcript(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    return rows(conn, "session_transcript", session_id=session_id)
