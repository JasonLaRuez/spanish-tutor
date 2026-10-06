"""SQLite connection and schema setup for the word bank."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from spanish_tutor.config import DB_PATH, SQL_DIR


def connect(path: Path | str = DB_PATH, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open the database with foreign keys enforced (SQLite leaves them off by default).

    check_same_thread=False lets a connection move between threads (the web server's
    workers); the caller must then make sure only one thread uses it at a time.
    """
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


MIGRATIONS_DIR = SQL_DIR / "migrations"


def migrations() -> list[tuple[int, Path]]:
    """Numbered migration files ("001_name.sql"), in order."""
    return sorted((int(path.name.split("_", 1)[0]), path) for path in MIGRATIONS_DIR.glob("*.sql"))


def pending_migrations(conn: sqlite3.Connection) -> list[int]:
    """Migration numbers an existing database still needs (none for a new database)."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'lexemes'").fetchone():
        return []
    applied = conn.execute("PRAGMA user_version").fetchone()[0]
    return [number for number, _ in migrations() if number > applied]


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the schema in a new database, or upgrade an existing one. Safe to rerun.

    schema.sql describes the latest schema, so a new database is created from it and
    stamped with the latest migration number. An existing database gets each migration
    it hasn't had yet (tracked in PRAGMA user_version), each in its own transaction.

    A migration that rebuilds a table other tables reference (SQLite can't alter a CHECK
    constraint) says so with a "-- foreign_keys: off" line. Foreign keys can only be
    switched off outside a transaction, so it runs with them off, and every foreign key
    is checked before the transaction commits: a broken reference rolls it back.
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
            if FOREIGN_KEYS_OFF in sql:
                _migrate_without_foreign_keys(conn, number, sql)
            else:
                conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    conn.executescript((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
    if is_new and (all_migrations := migrations()):
        conn.execute(f"PRAGMA user_version = {all_migrations[-1][0]}")


FOREIGN_KEYS_OFF = "-- foreign_keys: off"


def _migrate_without_foreign_keys(conn: sqlite3.Connection, number: int, sql: str) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        # One transaction, left open by executescript (which only commits *before* it
        # runs), so the check below sees the migrated tables and can still roll them back.
        conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};")
        if broken := conn.execute("PRAGMA foreign_key_check").fetchall():
            conn.execute("ROLLBACK")
            raise sqlite3.IntegrityError(
                f"migration {number} would break {len(broken)} foreign keys: {broken[:5]}"
            )
        conn.execute("COMMIT")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def statements(script: str) -> list[str]:
    """Split a SQL script into its statements.

    For running a script inside the caller's transaction, one statement at a time
    (executescript commits any open transaction first).
    """
    result, current = [], ""
    for line in script.splitlines(keepends=True):
        current += line
        if sqlite3.complete_statement(current):
            result.append(current.strip())
            current = ""
    if current.strip() and not all(
        line.strip().startswith("--") or not line.strip() for line in current.splitlines()
    ):
        raise ValueError(f"incomplete SQL statement at the end of the script: {current!r}")
    return result


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


def start_session(
    conn: sqlite3.Connection, skill: str, model: str, topic: str | None = None
) -> int:
    return conn.execute(
        "INSERT INTO sessions (skill, topic, model) VALUES (?, ?, ?)", (skill, topic, model)
    ).lastrowid


def end_session(conn: sqlite3.Connection, session_id: int) -> None:
    """Record that the learner ended the session (only the first time)."""
    conn.execute(
        "UPDATE sessions SET ended_at = CURRENT_TIMESTAMP "
        "WHERE session_id = ? AND ended_at IS NULL",
        (session_id,),
    )


def add_summary(
    conn: sqlite3.Connection,
    session_id: int,
    went_well_en: str,
    work_on: list[str],
    model: str,
    **costs: int,
) -> None:
    """Store the tutor's end-of-conversation notes; `costs` are the token and latency columns."""
    if unknown := set(costs) - {"input_tokens", "cache_read_tokens", "output_tokens", "latency_ms"}:
        raise TypeError(f"Unknown summary columns: {sorted(unknown)}")
    names = ["session_id", "went_well_en", "work_on_en", "model", *costs]
    conn.execute(
        f"INSERT INTO session_summaries ({', '.join(names)}) "
        f"VALUES ({', '.join('?' * len(names))})",
        (session_id, went_well_en, "\n".join(work_on), model, *costs.values()),
    )


TURN_COLUMNS = (
    "kind",
    "note_en",
    "draft_out_of_bank",
    "final_out_of_bank",
    "retried",
    "input_tokens",
    "cache_read_tokens",
    "cache_write_5m_tokens",
    "cache_write_1h_tokens",
    "output_tokens",
    "latency_ms",
)


def add_turn(
    conn: sqlite3.Connection,
    session_id: int,
    turn_no: int,
    role: str,
    text_es: str,
    **columns: object,
) -> int:
    """Append one message to the transcript. `columns` are the optional TURN_COLUMNS."""
    if unknown := set(columns) - set(TURN_COLUMNS):
        raise TypeError(f"Unknown turn columns: {sorted(unknown)}")
    names = ["session_id", "turn_no", "role", "text_es", *columns]
    return conn.execute(
        f"INSERT INTO turns ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
        (session_id, turn_no, role, text_es, *columns.values()),
    ).lastrowid


def rebuild_word_bank(conn: sqlite3.Connection) -> None:
    """Recompute word_bank from the full event log (e.g. after a formula change)."""
    conn.executescript((SQL_DIR / "rebuild_word_bank.sql").read_text(encoding="utf-8"))
