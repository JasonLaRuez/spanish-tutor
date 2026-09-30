"""Seed pipeline: frequency estimation, example choice, and the generated SQL."""

from collections import Counter

import pytest

from spanish_tutor.db import connect, init_schema
from spanish_tutor.ingest.tatoeba import AnalyzedSentence
from spanish_tutor.seed import (
    Example,
    SeedWord,
    form_analysis_counts,
    lemma_frequencies,
    pick_examples,
    read_marks,
    seed_sql,
)

BAJO_ADJ, BAJO_ADP = ("bajo", "ADJ"), ("bajo", "ADP")
DE, EL = ("de", "ADP"), ("el", "DET")


def sentence(id, tokens, en="translation", es="texto"):
    return AnalyzedSentence(id=id, es=es, en=en, author="someone", tokens=tokens)


# --- Frequency -------------------------------------------------------------------------


def test_ambiguous_form_count_is_split_by_contextual_usage():
    corpus = [
        sentence(1, [("bajo", [BAJO_ADJ])]),
        sentence(2, [("bajo", [BAJO_ADP])]),
        sentence(3, [("bajo", [BAJO_ADP])]),
        sentence(4, [("bajo", [BAJO_ADP])]),
    ]
    occurrences, with_analysis = form_analysis_counts(corpus)
    freqs, dropped = lemma_frequencies(Counter({"bajo": 100}), occurrences, with_analysis)
    assert freqs[BAJO_ADJ] == pytest.approx(25)
    assert freqs[BAJO_ADP] == pytest.approx(75)
    assert dropped == 0


def test_contraction_credits_both_words_in_full():
    corpus = [sentence(1, [("del", [DE, EL])])]
    freqs, _ = lemma_frequencies(Counter({"del": 40}), *form_analysis_counts(corpus))
    assert freqs[DE] == pytest.approx(40)
    assert freqs[EL] == pytest.approx(40)


def test_names_contribute_nothing_and_unseen_forms_are_reported_as_dropped():
    corpus = [sentence(1, [("juan", [])])]
    freqs, dropped = lemma_frequencies(
        Counter({"juan": 50, "zzz": 7}), *form_analysis_counts(corpus)
    )
    assert freqs == Counter()
    assert dropped == 7


# --- Examples --------------------------------------------------------------------------

CASA, GATO, PERRO, RARO = ("casa", "NOUN"), ("gato", "NOUN"), ("perro", "NOUN"), ("raro", "ADJ")


def test_example_prefers_fully_comprehensible_then_short_then_low_id():
    corpus = [
        # Shortest, but contains a word the learner doesn't know.
        sentence(1, [("casa", [CASA]), ("raro", [RARO])]),
        # Fully known, but longer.
        sentence(3, [("casa", [CASA]), ("gato", [GATO]), ("perro", [PERRO])]),
        # Fully known and shorter than 3, but no English translation.
        sentence(2, [("casa", [CASA]), ("gato", [GATO])], en=None),
        # Fully known, same length as 3, higher id.
        sentence(4, [("casa", [CASA]), ("perro", [PERRO]), ("gato", [GATO])]),
    ]
    examples = pick_examples(corpus, wanted={CASA}, known={CASA, GATO, PERRO})
    assert examples[CASA].sentence_id == 3


def test_example_prefers_a_useful_length_over_a_fragment():
    known = {CASA, GATO, PERRO}
    words = [("casa", [CASA]), ("gato", [GATO]), ("perro", [PERRO])]
    corpus = [
        sentence(1, words[:1]),  # "Casa." -- a fragment
        sentence(2, words + words[1:]),  # five words
        sentence(3, words * 4),  # twelve words, too long
    ]
    assert pick_examples(corpus, wanted={CASA}, known=known)[CASA].sentence_id == 2


def test_word_without_any_translated_sentence_gets_no_example():
    corpus = [sentence(1, [("casa", [CASA])], en=None)]
    assert pick_examples(corpus, wanted={CASA}, known={CASA}) == {}


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


# --- Generated SQL ---------------------------------------------------------------------


@pytest.fixture
def conn():
    conn = connect(":memory:")
    init_schema(conn)
    yield conn
    conn.close()


SEED_WORDS = [
    SeedWord("casa", "NOUN", False, "house", Example(12, "Mi casa.", "My house.", "ana")),
    SeedWord("gato", "NOUN", True, "cat", None),
    SeedWord("l'o", "NOUN", False, None, None),  # quote in a value must be escaped
]


def word_bank(conn):
    rows = conn.execute(
        "SELECT l.lemma, b.mode, b.learned_via FROM word_bank AS b JOIN lexemes AS l "
        "USING (lexeme_id)"
    )
    return {tuple(row) for row in rows}


def test_seed_sql_loads_words_and_events(conn):
    conn.executescript(seed_sql(SEED_WORDS))

    assert word_bank(conn) == {
        ("casa", "recognition", "seed"),
        ("gato", "recognition", "seed"),
        ("gato", "production", "seed"),
        ("l'o", "recognition", "seed"),
    }
    casa = conn.execute("SELECT * FROM lexemes WHERE lemma = 'casa'").fetchone()
    assert casa["definition_en"] == "house"
    assert casa["definition_source"] == "wiktionary"
    assert (casa["example_source"], casa["example_author"]) == ("tatoeba:12", "ana")
    gato = conn.execute("SELECT * FROM lexemes WHERE lemma = 'gato'").fetchone()
    assert gato["example_source"] is None


def test_seed_sql_is_safe_to_rerun(conn):
    conn.executescript(seed_sql(SEED_WORDS))
    conn.executescript(seed_sql(SEED_WORDS))

    assert conn.execute("SELECT COUNT(*) FROM lexemes").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM word_events").fetchone()[0] == 4


def test_seed_sql_fills_blanks_without_overwriting_existing_lexeme_data(conn):
    conn.execute("INSERT INTO lexemes (lemma, pos, definition_en) VALUES ('casa', 'NOUN', 'home')")
    conn.executescript(seed_sql(SEED_WORDS))

    casa = conn.execute("SELECT * FROM lexemes WHERE lemma = 'casa'").fetchone()
    assert casa["definition_en"] == "home"  # existing value kept
    assert casa["example_es"] == "Mi casa."  # blank filled
