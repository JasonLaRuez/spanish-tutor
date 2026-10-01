"""Behavior of sql/schema.sql: constraints on the log, and the derived word_bank table."""

import random
import sqlite3

import pytest

from spanish_tutor.db import connect, init_schema, rebuild_word_bank


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
