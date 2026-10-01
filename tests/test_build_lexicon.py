"""The general lexicon: frequency estimation, example choice, and the additive fill."""

import json
from collections import Counter

import pytest

from spanish_tutor.db import connect, init_schema
from spanish_tutor.ingest.build_lexicon import (
    Example,
    LexiconEntry,
    build_entries,
    fill_lexicon,
    form_analysis_counts,
    lemma_frequencies,
    pick_examples,
)
from spanish_tutor.ingest.tatoeba import AnalyzedSentence
from spanish_tutor.ingest.wiktionary import Wiktionary

BAJO_ADJ, BAJO_ADP = ("bajo", "ADJ"), ("bajo", "ADP")
DE, EL = ("de", "ADP"), ("el", "DET")
CASA, GATO, PERRO, RARO = ("casa", "NOUN"), ("gato", "NOUN"), ("perro", "NOUN"), ("raro", "ADJ")


def sentence(id, tokens, en="translation", es="texto"):
    return AnalyzedSentence(id=id, es=es, en=en, author="someone", tokens=tokens)


def quiet(_):
    pass


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


def test_example_prefers_fully_comprehensible_then_short_then_low_id():
    corpus = [
        # Shortest, but contains a word outside the known set.
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


# --- Building entries ------------------------------------------------------------------


def test_entries_cover_corpus_words_that_wiktionary_knows(tmp_path):
    wikt_path = tmp_path / "wikt.jsonl"
    wikt_path.write_text(
        "".join(
            json.dumps({"word": w, "pos": p, "senses": [{"gloss": g, "tags": []}]}) + "\n"
            for w, p, g in [("casa", "noun", "house"), ("gato", "noun", "cat")]
        ),
        encoding="utf-8",
    )
    corpus = [
        sentence(1, [("casa", [CASA]), ("gato", [GATO])], es="Casa gato."),
        sentence(2, [("crees", [("crees", "VERB")])]),  # tagger junk: no Wiktionary entry
        sentence(3, [("gato", [GATO])], en=None),
    ]
    entries = build_entries(
        lambda: corpus, Counter({"casa": 30, "gato": 10}), Wiktionary(wikt_path), report=quiet
    )
    by_word = {(e.lemma, e.pos): e for e in entries}

    assert set(by_word) == {CASA, GATO}
    assert [(e.lemma, e.pos) for e in entries] == [CASA, GATO]  # most frequent first
    assert by_word[CASA].frequency_per_million == pytest.approx(30 / 40 * 1e6)
    assert by_word[GATO].definition_en == "cat"
    assert by_word[GATO].example.sentence_id == 1


# --- Filling lexemes -------------------------------------------------------------------


@pytest.fixture
def conn():
    conn = connect(":memory:")
    init_schema(conn)
    yield conn
    conn.close()


ENTRIES = [
    LexiconEntry("casa", "NOUN", 900.0, "house", Example(12, "Mi casa.", "My house.", "ana")),
    LexiconEntry("estación", "NOUN", 40.0, "station", None),
    LexiconEntry("l'o", "NOUN", None, None, None),  # quotes and missing data are fine
]


def lexeme(conn, lemma):
    return conn.execute("SELECT * FROM lexemes WHERE lemma = ?", (lemma,)).fetchone()


def test_fill_adds_entries_with_provenance(conn):
    assert fill_lexicon(conn, ENTRIES) == (3, 0)
    casa = lexeme(conn, "casa")
    assert casa["frequency_per_million"] == 900.0
    assert (casa["definition_en"], casa["definition_source"]) == ("house", "wiktionary")
    assert (casa["example_source"], casa["example_author"]) == ("tatoeba:12", "ana")
    assert lexeme(conn, "l'o")["definition_source"] is None


def test_refill_keeps_ids_stable_and_adds_nothing(conn):
    fill_lexicon(conn, ENTRIES)
    ids = dict(conn.execute("SELECT lemma, lexeme_id FROM lexemes").fetchall())
    assert fill_lexicon(conn, ENTRIES) == (0, 3)
    assert dict(conn.execute("SELECT lemma, lexeme_id FROM lexemes").fetchall()) == ids


def test_fill_keeps_existing_data_and_learner_events(conn):
    conn.execute(
        "INSERT INTO lexemes (lemma, pos, definition_en, definition_source, example_es, "
        "example_en, example_source, example_author) VALUES ('casa', 'NOUN', 'home', "
        "'wiktionary+reviewed', 'La casa es grande.', 'The house is big.', 'tatoeba:7', 'bo')"
    )
    conn.execute(
        "INSERT INTO word_events (lexeme_id, mode, event_type, source) "
        "SELECT lexeme_id, 'recognition', 'taught', 'seed' FROM lexemes WHERE lemma = 'casa'"
    )
    fill_lexicon(conn, ENTRIES)

    casa = lexeme(conn, "casa")
    assert (casa["definition_en"], casa["definition_source"]) == ("home", "wiktionary+reviewed")
    # The existing example is kept as a unit: sentence, translation and author together.
    assert (casa["example_es"], casa["example_en"], casa["example_author"]) == (
        "La casa es grande.",
        "The house is big.",
        "bo",
    )
    assert casa["frequency_per_million"] == 900.0  # derived data is refreshed
    assert conn.execute("SELECT COUNT(*) FROM word_bank").fetchone()[0] == 1
