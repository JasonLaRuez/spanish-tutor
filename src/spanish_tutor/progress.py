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


def readiness(conn: sqlite3.Connection) -> list[dict]:
    """Vocabulary readiness per CEFR level (ELELex), with running totals up to each level."""
    return rows(conn, "readiness_by_level")


def growth_by_session(conn: sqlite3.Connection) -> list[dict]:
    return rows(conn, "progress_by_session")


def try_using(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Recognized words never used yet, most frequent first."""
    return rows(conn, "progress_gap", limit=limit)


def sessions(conn: sqlite3.Connection) -> list[dict]:
    return rows(conn, "sessions_summary")


def transcript(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    return rows(conn, "session_transcript", session_id=session_id)


def session_stats(conn: sqlite3.Connection, session_id: int) -> dict | None:
    """The stats for a session's end-of-conversation summary; None if there's no session.

    The word lists (first_time, pre_taught, pre_taught_used, practice, practice_first_use)
    come back as lists.
    """
    found = rows(conn, "session_stats", session_id=session_id)
    if not found:
        return None
    stats = found[0]
    for key in ("first_time", "pre_taught", "pre_taught_used", "practice", "practice_first_use"):
        stats[key] = stats[key].split(", ") if stats[key] else []
    return stats


def summary(conn: sqlite3.Connection, session_id: int) -> dict | None:
    """The tutor's stored notes on a session, or None if it has none."""
    row = conn.execute(
        "SELECT went_well_en, work_on_en FROM session_summaries WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return {"went_well_en": row[0], "work_on": [line for line in row[1].splitlines() if line]}
