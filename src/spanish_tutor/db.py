"""SQLite connection and schema setup for the word bank."""

import sqlite3
from pathlib import Path

from spanish_tutor.config import DB_PATH, SQL_DIR


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Open the database with foreign keys enforced (SQLite leaves them off by default)."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables, indexes, and views. Safe to run repeatedly."""
    conn.executescript((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))


def known_vocabulary(conn: sqlite3.Connection, mode: str = "recognition") -> set[tuple[str, str]]:
    """The learner's (lemma, pos) pairs in one mode: 'recognition' or 'production'."""
    rows = conn.execute(
        """
        SELECT l.lemma, l.pos
        FROM word_bank AS b
        JOIN lexemes AS l ON l.lexeme_id = b.lexeme_id
        WHERE b.mode = ?
        """,
        (mode,),
    )
    return {(lemma, pos) for lemma, pos in rows}


def rebuild_word_bank(conn: sqlite3.Connection) -> None:
    """Recompute word_bank from the full event log (e.g. after a formula change)."""
    conn.executescript((SQL_DIR / "rebuild_word_bank.sql").read_text(encoding="utf-8"))
