"""SQLite connection and schema setup for the word bank."""

import sqlite3
from datetime import UTC, datetime
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


MIGRATIONS_DIR = SQL_DIR / "migrations"


def migrations() -> list[tuple[int, Path]]:
    """Numbered migration files ("001_name.sql"), in order."""
    return sorted((int(path.name.split("_", 1)[0]), path) for path in MIGRATIONS_DIR.glob("*.sql"))


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the schema in a new database, or upgrade an existing one. Safe to rerun.

    schema.sql describes the latest schema, so a new database is created from it and
    stamped with the latest migration number. An existing database gets each migration
    it hasn't had yet (tracked in PRAGMA user_version), each in its own transaction.
    """
    is_new = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'lexemes'"
        ).fetchone()
        is None
    )
    applied = conn.execute("PRAGMA user_version").fetchone()[0]
    pending = migrations() if not is_new else []
    for number, path in pending:
        if number > applied:
            sql = path.read_text(encoding="utf-8")
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    conn.executescript((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
    if is_new and (all_migrations := migrations()):
        conn.execute(f"PRAGMA user_version = {all_migrations[-1][0]}")


def backup(path: Path = DB_PATH) -> Path | None:
    """Copy the database next to itself with a timestamp; None if there is nothing to copy.

    Uses SQLite's online backup API, which is safe even while the database is open.
    """
    path = Path(path)
    if not path.exists():
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
    dest = path.with_name(f"{path.stem}.backup-{stamp}{path.suffix}")
    source, target = sqlite3.connect(path), sqlite3.connect(dest)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()
    return dest


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
