"""Seeding: reading the learner's marks and recording them as events on the lexicon."""

import pytest

from spanish_tutor.db import connect, init_schema
from spanish_tutor.seed import SeedWord, candidates, missing_from_lexicon, read_marks, seed_sql

CASA, GATO, RARO = ("casa", "NOUN"), ("gato", "NOUN"), ("raro", "ADJ")


# --- Marks -----------------------------------------------------------------------------


def write_candidates(path, rows):
    lines = ["rank,lemma,pos,definition_en,example_es,example_en,known"]
    lines += [f"{i},{lemma},{pos},,,,{mark}" for i, (lemma, pos, mark) in enumerate(rows, 1)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def test_read_marks_accepts_r_and_p_case_insensitively(tmp_path):
    path = tmp_path / "candidates.csv"
    write_candidates(path, [("casa", "NOUN", "r"), ("gato", "NOUN", " P "), ("raro", "ADJ", "")])
    assert read_marks(path) == {CASA: False, GATO: True}


def test_read_marks_rejects_unknown_marks(tmp_path):
    path = tmp_path / "candidates.csv"
    write_candidates(path, [("casa", "NOUN", "yes")])
    with pytest.raises(SystemExit, match="casa"):
        read_marks(path)


# --- Lexicon queries -------------------------------------------------------------------


@pytest.fixture
def conn():
    conn = connect(":memory:")
    init_schema(conn)
    conn.executemany(
        "INSERT INTO lexemes (lemma, pos, frequency_per_million) VALUES (?, ?, ?)",
        [("casa", "NOUN", 900.0), ("gato", "NOUN", 300.0), ("l'o", "NOUN", 5.0),
         ("raro", "ADJ", None)],
    )  # fmt: skip
    yield conn
    conn.close()


def test_candidates_are_the_most_frequent_words(conn):
    assert [(r["lemma"], r["pos"]) for r in candidates(conn, top=2)] == [CASA, GATO]


def test_words_without_frequency_are_not_candidates(conn):
    assert RARO not in {(r["lemma"], r["pos"]) for r in candidates(conn, top=10)}


def test_marked_words_missing_from_the_lexicon_are_found(conn):
    assert missing_from_lexicon(conn, [CASA, ("perro", "NOUN")]) == [("perro", "NOUN")]


# --- Generated SQL ---------------------------------------------------------------------

SEED_WORDS = [
    SeedWord("casa", "NOUN", False),
    SeedWord("gato", "NOUN", True),
    SeedWord("l'o", "NOUN", False),  # quote in a value must be escaped
]


def word_bank(conn):
    rows = conn.execute(
        "SELECT l.lemma, b.mode, b.learned_via FROM word_bank AS b JOIN lexemes AS l "
        "USING (lexeme_id)"
    )
    return {tuple(row) for row in rows}


def test_seed_sql_records_marked_words_as_known(conn):
    conn.executescript(seed_sql(SEED_WORDS))

    assert word_bank(conn) == {
        ("casa", "recognition", "seed"),
        ("gato", "recognition", "seed"),
        ("gato", "production", "seed"),
        ("l'o", "recognition", "seed"),
    }


def test_seed_sql_is_safe_to_rerun(conn):
    conn.executescript(seed_sql(SEED_WORDS))
    conn.executescript(seed_sql(SEED_WORDS))

    assert conn.execute("SELECT COUNT(*) FROM lexemes").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM word_events").fetchone()[0] == 4
