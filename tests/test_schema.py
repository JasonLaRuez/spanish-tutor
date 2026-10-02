"""Behavior of sql/schema.sql: constraints on the log, and the derived word_bank table."""

import random
import sqlite3
from pathlib import Path

import pytest

from spanish_tutor.db import connect, init_schema, rebuild_word_bank, statements

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn():
    conn = connect(":memory:")
    init_schema(conn)
    yield conn
    conn.close()


def add_lexeme(conn, lemma, pos="NOUN"):
    cur = conn.execute("INSERT INTO lexemes (lemma, pos) VALUES (?, ?)", (lemma, pos))
    return cur.lastrowid


def add_event(conn, lexeme_id, event_type, *, grade=None, source="conversation", at=None):
    mode = "production" if event_type == "used" else "recognition"
    conn.execute(
        """
        INSERT INTO word_events (lexeme_id, mode, event_type, source, grade, occurred_at)
        VALUES (?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
        """,
        (lexeme_id, mode, event_type, source, grade, at),
    )


def bank_rows(conn):
    return {
        (row["lemma"], row["mode"]): row
        for row in conn.execute(
            "SELECT b.*, l.lemma FROM word_bank AS b JOIN lexemes AS l USING (lexeme_id)"
        )
    }


def snapshot(conn, source):
    return conn.execute(f"SELECT * FROM {source} ORDER BY lexeme_id, mode").fetchall()


def test_init_schema_is_idempotent(conn):
    init_schema(conn)


def test_same_lemma_with_different_pos_are_distinct_lexemes(conn):
    add_lexeme(conn, "bajo", "ADJ")
    add_lexeme(conn, "bajo", "ADP")
    with pytest.raises(sqlite3.IntegrityError):
        add_lexeme(conn, "bajo", "ADJ")


def test_multiword_expressions_are_allowed(conn):
    add_lexeme(conn, "echar de menos", "EXPR")


@pytest.mark.parametrize("pos", ["AUX", "PROPN", "PUNCT", "noun"])
def test_non_vocabulary_pos_tags_are_rejected(conn, pos):
    with pytest.raises(sqlite3.IntegrityError):
        add_lexeme(conn, "ser", pos)


def test_foreign_keys_are_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        add_event(conn, 999, "taught")


def test_event_type_must_match_mode(conn):
    lexeme_id = add_lexeme(conn, "casa")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO word_events (lexeme_id, mode, event_type, source) "
            "VALUES (?, 'production', 'taught', 'seed')",
            (lexeme_id,),
        )


def test_taught_word_enters_recognition_bank_only(conn):
    casa = add_lexeme(conn, "casa")
    add_event(conn, casa, "taught", source="seed")

    rows = bank_rows(conn)
    assert set(rows) == {("casa", "recognition")}
    assert rows["casa", "recognition"]["learned_via"] == "seed"
    assert rows["casa", "recognition"]["familiarity"] is None  # not assessed yet


def test_seeing_an_untaught_word_does_not_add_it(conn):
    perro = add_lexeme(conn, "perro")
    add_event(conn, perro, "seen", grade=5)
    assert bank_rows(conn) == {}


def test_using_a_word_adds_a_production_row(conn):
    casa = add_lexeme(conn, "casa")
    add_event(conn, casa, "taught", source="pre_teach", at="2026-10-01 10:00:00")
    add_event(conn, casa, "used", grade=4, at="2026-10-02 10:00:00")

    rows = bank_rows(conn)
    assert set(rows) == {("casa", "recognition"), ("casa", "production")}
    production = rows["casa", "production"]
    assert production["learned_at"] == "2026-10-02 10:00:00"
    assert production["familiarity"] == pytest.approx(0.8)
    # The production grade must not leak into the recognition score.
    assert rows["casa", "recognition"]["familiarity"] is None


def test_learned_via_comes_from_the_first_teaching(conn):
    casa = add_lexeme(conn, "casa")
    add_event(conn, casa, "taught", source="reading", at="2026-10-05 10:00:00")
    add_event(conn, casa, "taught", source="seed", at="2026-10-01 10:00:00")

    row = bank_rows(conn)["casa", "recognition"]
    assert row["learned_via"] == "seed"
    assert row["learned_at"] == "2026-10-01 10:00:00"


def test_familiarity_uses_only_the_five_most_recent_grades(conn):
    casa = add_lexeme(conn, "casa")
    add_event(conn, casa, "taught", at="2026-10-01 00:00:00")
    # Two old failures, then five recent perfect scores.
    for day, grade in enumerate([0, 0, 5, 5, 5, 5, 5], start=2):
        add_event(conn, casa, "seen", grade=grade, at=f"2026-10-{day:02d} 00:00:00")

    row = bank_rows(conn)["casa", "recognition"]
    assert row["familiarity"] == pytest.approx(1.0)
    assert row["encounters"] == 8
    assert row["last_seen_at"] == "2026-10-08 00:00:00"


def test_trigger_matches_full_rebuild_on_random_history(conn):
    """The incremental trigger and the full-history view must never disagree."""
    rnd = random.Random(1234)
    lexeme_ids = [add_lexeme(conn, f"palabra{i}") for i in range(20)]
    for _ in range(500):
        event_type = rnd.choice(["taught", "seen", "seen", "looked_up", "used"])
        grade = None if event_type == "taught" else rnd.choice([None, 0, 2, 3, 4, 5])
        # Random timestamps, so events regularly arrive out of chronological order.
        at = f"2026-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d} 00:00:00"
        add_event(conn, rnd.choice(lexeme_ids), event_type, grade=grade, at=at)

    live = snapshot(conn, "word_bank")
    assert len(live) > 0
    assert live == snapshot(conn, "word_bank_rebuild")


def test_rebuild_restores_word_bank_from_log(conn):
    casa = add_lexeme(conn, "casa")
    add_event(conn, casa, "taught", source="seed", at="2026-10-01 10:00:00")
    add_event(conn, casa, "used", grade=3, at="2026-10-02 10:00:00")
    before = snapshot(conn, "word_bank")

    conn.execute("DELETE FROM word_bank")
    rebuild_word_bank(conn)

    assert snapshot(conn, "word_bank") == before


def test_lexemes_record_provenance_for_attribution(conn):
    conn.execute(
        """
        INSERT INTO lexemes (lemma, pos, definition_en, definition_source,
                             example_es, example_en, example_source, example_author)
        VALUES ('casa', 'NOUN', 'house', 'wiktionary',
                'Mi casa es tu casa.', 'My house is your house.', 'tatoeba:12345', 'someone')
        """
    )
    row = conn.execute("SELECT * FROM lexemes WHERE lemma = 'casa'").fetchone()
    assert row["example_source"] == "tatoeba:12345"
    assert row["example_author"] == "someone"
    assert row["definition_source"] == "wiktionary"


def test_known_vocabulary_separates_recognition_and_production(conn):
    from spanish_tutor.db import known_vocabulary

    casa, gato = add_lexeme(conn, "casa"), add_lexeme(conn, "gato")
    add_lexeme(conn, "perro")  # in lexemes, never taught
    add_event(conn, casa, "taught")
    add_event(conn, gato, "taught")
    add_event(conn, gato, "used", grade=4)

    assert known_vocabulary(conn) == {("casa", "NOUN"), ("gato", "NOUN")}
    assert known_vocabulary(conn, "production") == {("gato", "NOUN")}


# --- Migrations ------------------------------------------------------------------------


def test_new_database_is_created_at_the_latest_version(conn):
    from spanish_tutor.db import migrations

    assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations()[-1][0]
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(lexemes)")}
    assert "frequency_per_million" in columns


def test_database_created_before_migration_1_is_upgraded_without_data_loss():
    from spanish_tutor.db import migrations

    old = connect(":memory:")
    # The lexemes and word_events tables as they were before migration 1, holding a word.
    old.executescript(
        """
        CREATE TABLE lexemes (
            lexeme_id INTEGER PRIMARY KEY, lemma TEXT NOT NULL, pos TEXT NOT NULL,
            cefr_level TEXT, definition_en TEXT, definition_es TEXT,
            definition_source TEXT, example_es TEXT, example_en TEXT,
            example_source TEXT, example_author TEXT, UNIQUE (lemma, pos)
        );
        CREATE TABLE word_events (
            event_id INTEGER PRIMARY KEY, lexeme_id INTEGER NOT NULL REFERENCES lexemes,
            mode TEXT NOT NULL, event_type TEXT NOT NULL, source TEXT NOT NULL,
            grade INTEGER, occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO lexemes (lemma, pos, definition_en) VALUES ('casa', 'NOUN', 'house');
        """
    )
    init_schema(old)
    init_schema(old)  # re-running applies nothing twice

    assert old.execute("PRAGMA user_version").fetchone()[0] == migrations()[-1][0]
    row = old.execute("SELECT * FROM lexemes").fetchone()
    assert (row["lemma"], row["definition_en"], row["frequency_per_million"]) == (
        "casa",
        "house",
        None,
    )
    tables = {r["name"] for r in old.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"lexeme_reviews", "word_events", "word_bank"} <= tables


def table_shapes(conn):
    """Every table's columns (name, type, notnull, default, pk) and foreign keys."""
    names = [
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
    ]
    return {
        name: (
            sorted(tuple(c)[1:] for c in conn.execute(f"PRAGMA table_info({name})")),
            sorted(tuple(f)[2:5] for f in conn.execute(f"PRAGMA foreign_key_list({name})")),
        )
        for name in names
    }


def test_version_1_database_upgrades_to_the_same_shape_as_a_new_one(conn):
    old = connect(":memory:")
    old.executescript((FIXTURES / "schema_v1.sql").read_text(encoding="utf-8"))
    old.execute("PRAGMA user_version = 1")
    casa = add_lexeme(old, "casa")
    add_event(old, casa, "taught", source="seed")

    init_schema(old)

    assert table_shapes(old) == table_shapes(conn)
    assert old.execute("SELECT turn_id FROM word_events").fetchone()[0] is None
    assert [r["lemma"] for r in old.execute("SELECT lemma FROM lexemes")] == ["casa"]


# --- Sessions and turns ----------------------------------------------------------------


def add_session(conn):
    return conn.execute(
        "INSERT INTO sessions (skill, topic, model) VALUES ('conversation', 'el tiempo', 'm')"
    ).lastrowid


def add_turn(conn, session_id, turn_no, role="learner", text="Hola."):
    return conn.execute(
        "INSERT INTO turns (session_id, turn_no, role, text_es) VALUES (?, ?, ?, ?)",
        (session_id, turn_no, role, text),
    ).lastrowid


def test_turn_numbers_are_unique_within_a_session(conn):
    first, second = add_session(conn), add_session(conn)
    add_turn(conn, first, 1)
    add_turn(conn, second, 1)
    with pytest.raises(sqlite3.IntegrityError):
        add_turn(conn, first, 1)


@pytest.mark.parametrize(
    "column, value",
    [("role", "system"), ("turn_no", 0), ("retried", 2), ("draft_out_of_bank", -1)],
)
def test_turn_values_are_checked(conn, column, value):
    session = add_session(conn)
    turn = add_turn(conn, session, 1)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"UPDATE turns SET {column} = ? WHERE turn_id = ?", (value, turn))


def test_sessions_must_name_a_known_skill(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO sessions (skill, model) VALUES ('chat', 'm')")


def test_events_can_reference_the_turn_that_caused_them(conn):
    casa = add_lexeme(conn, "casa")
    turn = add_turn(conn, add_session(conn), 1)
    conn.execute(
        "INSERT INTO word_events (lexeme_id, mode, event_type, source, turn_id) "
        "VALUES (?, 'recognition', 'taught', 'conversation', ?)",
        (casa, turn),
    )
    add_event(conn, casa, "seen")  # turn_id is optional
    assert conn.execute("SELECT encounters FROM word_bank").fetchone()[0] == 2
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO word_events (lexeme_id, mode, event_type, source, turn_id) "
            "VALUES (?, 'recognition', 'seen', 'conversation', 999)",
            (casa,),
        )


def test_lexeme_reviews_record_verdicts_with_their_reviewer(conn):
    casa = add_lexeme(conn, "casa")
    conn.execute(
        "INSERT INTO lexeme_reviews (lexeme_id, field, verdict, suggestion, reviewer) "
        "VALUES (?, 'definition_en', 'incorrect', 'house, home', 'claude-opus-5-5')",
        (casa,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO lexeme_reviews (lexeme_id, field, verdict, reviewer) "
            "VALUES (?, 'definition_en', 'maybe', 'human')",
            (casa,),
        )


def test_pending_migrations_lists_only_what_an_old_database_needs(conn):
    from spanish_tutor.db import pending_migrations

    assert pending_migrations(conn) == []  # created at the latest version
    assert pending_migrations(connect(":memory:")) == []  # empty: schema.sql creates it
    old = connect(":memory:")
    old.executescript((FIXTURES / "schema_v1.sql").read_text(encoding="utf-8"))
    old.execute("PRAGMA user_version = 1")
    assert pending_migrations(old) == [2, 3]


def test_version_2_database_upgrades_to_version_3_keeping_notes(conn):
    old = connect(":memory:")
    old.executescript((FIXTURES / "schema_v2.sql").read_text(encoding="utf-8"))
    old.execute("PRAGMA user_version = 2")
    session = add_session(old)
    old.execute(
        "INSERT INTO turns (session_id, turn_no, role, text_es, correction_en) "
        "VALUES (?, 1, 'tutor', 'Hola.', 'Use estar.')",
        (session,),
    )

    init_schema(old)

    assert table_shapes(old) == table_shapes(conn)
    row = old.execute("SELECT note_en, kind FROM turns").fetchone()
    assert (row["note_en"], row["kind"]) == ("Use estar.", "conversation")


def test_turn_kind_is_checked(conn):
    session = add_session(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO turns (session_id, turn_no, role, text_es, kind) "
            "VALUES (?, 1, 'tutor', 'Hola.', 'chat')",
            (session,),
        )


def test_statements_splits_a_script_keeping_comments_and_quoted_semicolons():
    script = "-- first\nSELECT 'a;\nb';\n\n-- second\nSELECT 2;\n-- trailing comment\n"
    assert statements(script) == ["-- first\nSELECT 'a;\nb';", "-- second\nSELECT 2;"]


def test_statements_rejects_an_unfinished_statement():
    with pytest.raises(ValueError, match="incomplete"):
        statements("SELECT 1;\nSELECT 2\n")
