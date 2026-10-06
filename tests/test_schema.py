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
    assert pending_migrations(old) == [2, 3, 4, 5, 6, 7, 8]


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


def test_existing_sessions_upgrade_to_version_4_as_not_ended(conn):
    old = connect(":memory:")
    old.executescript((FIXTURES / "schema_v2.sql").read_text(encoding="utf-8"))
    old.execute("PRAGMA user_version = 2")
    session = add_session(old)

    init_schema(old)

    assert table_shapes(old) == table_shapes(conn)
    assert old.execute("SELECT ended_at FROM sessions").fetchone()["ended_at"] is None
    assert old.execute("SELECT COUNT(*) FROM session_summaries").fetchone()[0] == 0
    assert session == 1


def test_a_session_has_at_most_one_summary_and_it_must_exist(conn):
    from spanish_tutor.db import add_summary, end_session

    session = add_session(conn)
    end_session(conn, session)
    first_end = conn.execute("SELECT ended_at FROM sessions").fetchone()["ended_at"]
    end_session(conn, session)  # ending twice keeps the first time
    assert first_end and conn.execute("SELECT ended_at FROM sessions").fetchone()[0] == first_end
    add_summary(conn, session, "Good.", ["Practice ser/estar.", "Use regar."], "m", output_tokens=9)
    row = conn.execute("SELECT work_on_en, output_tokens FROM session_summaries").fetchone()
    assert tuple(row) == ("Practice ser/estar.\nUse regar.", 9)
    with pytest.raises(sqlite3.IntegrityError):  # one summary per session
        add_summary(conn, session, "Again.", [], "m")
    with pytest.raises(sqlite3.IntegrityError):  # for a session that exists
        add_summary(conn, 99, "Nobody.", [], "m")


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


# --- Content and the difficulty index (migration 6) -------------------------------------


def database_at(version):
    """An existing database at an older migration: version 2's fixture, migrated by hand."""
    from spanish_tutor.db import migrations

    old = connect(":memory:")
    old.executescript((FIXTURES / "schema_v2.sql").read_text(encoding="utf-8"))
    for number, path in migrations():
        if 2 < number <= version:
            old.executescript(path.read_text(encoding="utf-8"))
    old.execute(f"PRAGMA user_version = {version}")
    return old


def test_version_5_database_upgrades_to_version_6_keeping_its_words(conn):
    from spanish_tutor.db import migrations

    old = database_at(5)
    casa = add_lexeme(old, "casa")
    add_event(old, casa, "taught", source="seed")

    init_schema(old)

    assert table_shapes(old) == table_shapes(conn)
    assert old.execute("PRAGMA user_version").fetchone()[0] == migrations()[-1][0]
    row = old.execute("SELECT lemma, example_en_source FROM lexemes").fetchone()
    assert tuple(row) == ("casa", None)
    assert old.execute("SELECT COUNT(*) FROM word_bank").fetchone()[0] == 1


def add_book(conn, title="Platero y yo"):
    return conn.execute(
        "INSERT INTO books (title, author, source, is_private) VALUES (?, 'J. R. Jiménez', "
        "'gutenberg:1', 0)",
        (title,),
    ).lastrowid


def add_chapter(conn, book_id, chapter_no):
    return conn.execute(
        "INSERT INTO content_items (kind, title, book_id, chapter_no, text_es) "
        "VALUES ('chapter', ?, ?, ?, 'Texto.')",
        (f"Capítulo {chapter_no}", book_id, chapter_no),
    ).lastrowid


def add_song(conn, title="Una canción"):
    return conn.execute(
        "INSERT INTO content_items (kind, title, author, source, is_private, text_es) "
        "VALUES ('song', ?, 'Alguien', 'private', 1, 'La la la.')",
        (title,),
    ).lastrowid


def test_chapters_belong_to_a_book_and_take_its_source(conn):
    book = add_book(conn)
    add_chapter(conn, book, 1)
    add_chapter(conn, book, 2)
    add_song(conn)
    add_song(conn, "Otra canción")  # many items without a book: NULLs never collide
    bad = [
        # A chapter without a book, or without a number.
        ("chapter", None, 3, None, None),
        ("chapter", book, None, None, None),
        # A chapter repeating its book's source or privacy.
        ("chapter", book, 3, "gutenberg:1", None),
        ("chapter", book, 3, None, 0),
        # A song or story inside a book, or without its own source and privacy.
        ("song", book, None, "private", 1),
        ("story", None, 1, "gutenberg:2", 0),
        ("story", None, None, None, 0),
        ("song", None, None, "private", None),
    ]
    for kind, book_id, chapter_no, source, is_private in bad:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO content_items (kind, title, book_id, chapter_no, source, "
                "is_private, text_es) VALUES (?, 'x', ?, ?, ?, ?, 'x')",
                (kind, book_id, chapter_no, source, is_private),
            )
    with pytest.raises(sqlite3.IntegrityError):  # a chapter number appears once per book
        add_chapter(conn, book, 2)
    add_chapter(conn, add_book(conn, "Otro libro"), 2)  # ... but in every book


def test_content_vocab_counts_each_word_once_per_item(conn):
    song = add_song(conn)
    casa = add_lexeme(conn, "casa")
    conn.execute("INSERT INTO content_vocab VALUES (?, ?, 3)", (song, casa))
    with pytest.raises(sqlite3.IntegrityError):  # one row per (item, word)
        conn.execute("INSERT INTO content_vocab VALUES (?, ?, 1)", (song, casa))
    with pytest.raises(sqlite3.IntegrityError):  # a word in the vocabulary occurs
        conn.execute("INSERT INTO content_vocab VALUES (?, ?, 0)", (song, add_lexeme(conn, "x")))
    with pytest.raises(sqlite3.IntegrityError):  # of an item that exists
        conn.execute("INSERT INTO content_vocab VALUES (99, ?, 1)", (casa,))


def test_only_a_start_says_how_the_item_was_chosen(conn):
    song = add_song(conn)
    sql = "INSERT INTO content_events (content_id, event, chosen_via) VALUES (?, ?, ?)"
    conn.execute(sql, (song, "started", "recommended"))
    conn.execute(sql, (song, "started", "requested"))
    conn.execute(sql, (song, "finished", None))
    for event, chosen_via in [("started", None), ("finished", "requested"), ("read", None)]:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql, (song, event, chosen_via))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, (song, "started", "surprise"))


def test_a_resolution_names_a_word_unless_the_form_is_not_spanish(conn):
    ser = add_lexeme(conn, "ser", "VERB")
    sql = (
        "INSERT INTO word_resolutions (form, tagged_lemma, tagged_pos, verdict, lexeme_id, "
        "reviewer) VALUES (?, ?, 'VERB', ?, ?, 'model:test')"
    )
    conn.execute(sql, ("fué", "fuar", "variant", ser))
    conn.execute(sql, ("yeah", "yeah", "not_spanish", None))
    for form, verdict, lexeme_id in [
        ("fué", "variant", None),  # a variant of nothing
        ("chamba", "word", None),  # a word with no row
        ("yeah", "not_spanish", ser),  # not Spanish, yet a Spanish word
        ("fué", "guess", ser),
    ]:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql, (form, form, verdict, lexeme_id))


# --- Migration 7: turns.kind widened by a table rebuild -----------------------------------


def test_version_6_database_rebuilds_turns_keeping_every_turn_and_event(conn):
    from spanish_tutor.db import migrations

    old = database_at(6)
    session = add_session(old)
    first = add_turn(old, session, 1, role="tutor", text="Hola.")
    old.execute(
        "UPDATE turns SET kind = 'translation', note_en = 'a note', output_tokens = 7 "
        "WHERE turn_id = ?",
        (first,),
    )
    second = add_turn(old, session, 2)
    casa = add_lexeme(old, "casa")
    old.execute(
        "INSERT INTO word_events (lexeme_id, mode, event_type, source, turn_id) "
        "VALUES (?, 'recognition', 'taught', 'conversation', ?)",
        (casa, second),
    )
    old.commit()
    before = old.execute("SELECT * FROM turns ORDER BY turn_id").fetchall()

    init_schema(old)

    assert table_shapes(old) == table_shapes(conn)
    assert old.execute("PRAGMA user_version").fetchone()[0] == migrations()[-1][0]
    # Same rows, compared by column: the rebuilt table has schema.sql's column order, while
    # an old database had later columns (kind, the cache writes) at the end.
    assert [dict(r) for r in old.execute("SELECT * FROM turns ORDER BY turn_id")] == [
        dict(r) for r in before
    ]
    assert old.execute("PRAGMA foreign_key_check").fetchall() == []
    assert old.execute("PRAGMA foreign_keys").fetchone()[0] == 1  # switched back on
    assert old.execute("SELECT turn_id FROM word_events").fetchone()[0] == second
    # The new kinds are allowed, and foreign keys still hold on the rebuilt table.
    add_turn(old, session, 3, role="tutor", text="perro, gato")
    old.execute("UPDATE turns SET kind = 'study' WHERE turn_no = 3")
    with pytest.raises(sqlite3.IntegrityError):
        old.execute(
            "INSERT INTO turns (session_id, turn_no, role, text_es) VALUES (99, 1, 'tutor', 'x')"
        )


def test_new_turn_kinds_are_accepted_and_others_refused(conn):
    session = add_session(conn)
    for n, kind in enumerate(("study", "reading", "attempt"), 1):
        conn.execute(
            "INSERT INTO turns (session_id, turn_no, role, kind, text_es) VALUES (?, ?, 'tutor', ?, 'x')",
            (session, n, kind),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO turns (session_id, turn_no, role, kind, text_es) VALUES (?, 9, 'tutor', 'quiz', 'x')",
            (session,),
        )


def test_a_migration_that_would_break_a_reference_is_rolled_back(conn):
    from spanish_tutor.db import _migrate_without_foreign_keys

    session = add_session(conn)
    turn = add_turn(conn, session, 1)
    casa = add_lexeme(conn, "casa")
    conn.execute(
        "INSERT INTO word_events (lexeme_id, mode, event_type, source, turn_id) "
        "VALUES (?, 'recognition', 'taught', 'conversation', ?)",
        (casa, turn),
    )
    conn.commit()
    version = conn.execute("PRAGMA user_version").fetchone()[0]

    with pytest.raises(sqlite3.IntegrityError, match="would break 1 foreign keys"):
        _migrate_without_foreign_keys(conn, 99, "DELETE FROM turns;")

    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1  # rolled back
    assert conn.execute("PRAGMA user_version").fetchone()[0] == version
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# --- Migration 8: poems, and stored translations ------------------------------------------


def test_version_7_database_rebuilds_content_items_keeping_everything_that_points_to_it(conn):
    old = database_at(7)
    book = add_book(old)
    chapter = add_chapter(old, book, 1)
    song = add_song(old)
    casa = add_lexeme(old, "casa")
    old.execute("INSERT INTO content_vocab VALUES (?, ?, 3)", (chapter, casa))
    old.execute(
        "INSERT INTO content_events (content_id, event, chosen_via) VALUES (?, 'started', 'requested')",
        (song,),
    )
    old.execute(
        "INSERT INTO word_resolutions (form, tagged_lemma, tagged_pos, verdict, lexeme_id, "
        "reviewer, content_id) VALUES ('casas', 'casa', 'NOUN', 'variant', ?, 'human', ?)",
        (casa, song),
    )
    old.commit()
    before = [dict(r) for r in old.execute("SELECT * FROM content_items ORDER BY content_id")]

    init_schema(old)

    assert table_shapes(old) == table_shapes(conn)
    assert old.execute("PRAGMA user_version").fetchone()[0] == 8
    assert [
        dict(r) for r in old.execute("SELECT * FROM content_items ORDER BY content_id")
    ] == before
    assert old.execute("PRAGMA foreign_key_check").fetchall() == []
    assert old.execute("SELECT COUNT(*) FROM content_vocab").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):  # still enforced on the rebuilt table
        old.execute("INSERT INTO content_vocab VALUES (99, ?, 1)", (casa,))


def test_a_poem_stands_alone_like_a_song(conn):
    conn.execute(
        "INSERT INTO content_items (kind, title, author, source, is_private, text_es) "
        "VALUES ('poem', 'Rima XXIII', 'Bécquer', 'gutenberg:53552', 0, 'Por una mirada, un mundo;')"
    )
    with pytest.raises(sqlite3.IntegrityError):  # a poem is not part of a book
        conn.execute(
            "INSERT INTO content_items (kind, title, book_id, chapter_no, text_es) "
            "VALUES ('poem', 'x', ?, 1, 'x')",
            (add_book(conn),),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO content_items (kind, title, source, is_private, text_es) "
            "VALUES ('essay', 'x', 'x', 0, 'x')"
        )


def test_a_translation_is_a_run_with_one_row_per_line(conn):
    poem = add_song(conn)
    run = conn.execute(
        "INSERT INTO song_translations (content_id, model, output_tokens) VALUES (?, 'm', 900)",
        (poem,),
    ).lastrowid
    line = "INSERT INTO song_translation_lines VALUES (?, ?, ?, ?, ?)"
    conn.execute(line, (run, 1, "For a look, a world;", "For one look, one world;", None))
    conn.execute(
        line, (run, 2, "for a smile, a sky;", "for one smile, one sky;", "cielo: sky or heaven")
    )
    with pytest.raises(sqlite3.IntegrityError):  # one row per line of a run
        conn.execute(line, (run, 2, "x", "x", None))
    with pytest.raises(sqlite3.IntegrityError):  # lines are numbered from 1
        conn.execute(line, (run, 0, "x", "x", None))
    with pytest.raises(sqlite3.IntegrityError):  # of a run that exists
        conn.execute(line, (99, 1, "x", "x", None))
    with pytest.raises(sqlite3.IntegrityError):  # for an item that exists
        conn.execute("INSERT INTO song_translations (content_id, model) VALUES (99, 'm')")
    with pytest.raises(sqlite3.IntegrityError):  # both translations are required
        conn.execute(line, (run, 3, "x", None, None))
