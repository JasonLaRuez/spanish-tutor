"""The general lexicon: frequency estimation, example choice, and the fill."""

import json
import sqlite3
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
    assert fill_lexicon(conn, ENTRIES) == (3, 0, 0, 0)
    casa = lexeme(conn, "casa")
    assert casa["frequency_per_million"] == 900.0
    assert (casa["definition_en"], casa["definition_source"]) == ("house", "wiktionary")
    assert (casa["example_source"], casa["example_author"]) == ("tatoeba:12", "ana")
    assert lexeme(conn, "l'o")["definition_source"] is None


def test_refill_keeps_ids_stable_and_adds_nothing(conn):
    fill_lexicon(conn, ENTRIES)
    ids = dict(conn.execute("SELECT lemma, lexeme_id FROM lexemes").fetchall())
    assert fill_lexicon(conn, ENTRIES) == (0, 3, 0, 0)
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


# --- Rows a rebuild no longer produces ------------------------------------------------


def add_old_row(conn, lemma, frequency=5.0):
    """A row from an earlier build that the current build (ENTRIES) doesn't produce."""
    return conn.execute(
        "INSERT INTO lexemes (lemma, pos, frequency_per_million, definition_en) "
        "VALUES (?, 'NOUN', ?, 'old')",
        (lemma, frequency),
    ).lastrowid


def test_unreferenced_rows_no_longer_built_are_removed(conn):
    add_old_row(conn, "tambien")
    counts = fill_lexicon(conn, ENTRIES)
    assert (counts.added, counts.removed, counts.cleared) == (3, 1, 0)
    assert lexeme(conn, "tambien") is None


def test_rows_with_history_are_kept_with_their_frequency_cleared(conn):
    mas = add_old_row(conn, "mas", frequency=94.5)
    conn.execute(
        "INSERT INTO word_events (lexeme_id, mode, event_type, source) "
        "VALUES (?, 'recognition', 'taught', 'seed')",
        (mas,),
    )
    counts = fill_lexicon(conn, ENTRIES)
    assert (counts.removed, counts.cleared) == (0, 1)
    row = lexeme(conn, "mas")
    assert row["lexeme_id"] == mas
    assert row["frequency_per_million"] is None
    assert row["definition_en"] == "old"  # only the build-derived frequency is cleared
    assert conn.execute("SELECT COUNT(*) FROM word_bank").fetchone()[0] == 1


def test_reviewed_rows_are_kept(conn):
    reviewed = add_old_row(conn, "razon")
    conn.execute(
        "INSERT INTO lexeme_reviews (lexeme_id, field, verdict, reviewer) "
        "VALUES (?, 'definition_en', 'correct', 'human')",
        (reviewed,),
    )
    assert fill_lexicon(conn, ENTRIES).removed == 0
    assert lexeme(conn, "razon")["lexeme_id"] == reviewed


def add_story(conn):
    return conn.execute(
        "INSERT INTO content_items (kind, title, source, is_private, text_es) "
        "VALUES ('story', 'Un cuento', 'gutenberg:1', 0, 'Había una vez.')"
    ).lastrowid


def test_rows_in_indexed_content_are_kept(conn):
    """Words added while indexing (from Wiktionary or by a model) aren't in any build."""
    indexed = add_old_row(conn, "fogón")
    conn.execute(
        "INSERT INTO content_vocab (content_id, lexeme_id, occurrences) VALUES (?, ?, 2)",
        (add_story(conn), indexed),
    )
    assert fill_lexicon(conn, ENTRIES).removed == 0
    assert lexeme(conn, "fogón")["lexeme_id"] == indexed


def test_rows_a_word_resolution_points_to_are_kept(conn):
    resolved = add_old_row(conn, "chamba")
    conn.execute(
        "INSERT INTO word_resolutions (form, tagged_lemma, tagged_pos, verdict, lexeme_id, "
        "reviewer) VALUES ('chamba', 'chamba', 'NOUN', 'word', ?, 'model:test')",
        (resolved,),
    )
    assert fill_lexicon(conn, ENTRIES).removed == 0
    assert lexeme(conn, "chamba")["lexeme_id"] == resolved


def test_a_filled_example_is_a_human_translation(conn):
    """example_en_source moves with the example: a Tatoeba example filled into a blank
    slot never keeps a stale "model-translated" mark, and a kept example keeps its own."""
    conn.execute(
        "INSERT INTO lexemes (lemma, pos, example_en_source) VALUES ('casa', 'NOUN', 'model:x')"
    )
    conn.execute(
        "INSERT INTO lexemes (lemma, pos, example_es, example_en, example_source, "
        "example_en_source) VALUES ('estación', 'NOUN', 'La estación.', 'The station.', "
        "'content:1', 'model:x')"
    )
    fill_lexicon(conn, ENTRIES)
    casa = lexeme(conn, "casa")
    assert (casa["example_source"], casa["example_en_source"]) == ("tatoeba:12", None)
    assert lexeme(conn, "estación")["example_en_source"] == "model:x"


def test_cleared_counts_only_frequencies_that_changed(conn):
    add_old_row(conn, "mas", frequency=94.5)
    conn.execute(
        "INSERT INTO word_events (lexeme_id, mode, event_type, source) "
        "SELECT lexeme_id, 'recognition', 'taught', 'seed' FROM lexemes WHERE lemma = 'mas'"
    )
    assert fill_lexicon(conn, ENTRIES).cleared == 1
    # A second build finds the frequency already cleared, and l'o (built, no frequency)
    # never counts.
    assert fill_lexicon(conn, ENTRIES) == (0, 3, 0, 0)


def test_unlisted_reference_rolls_the_whole_fill_back(conn):
    """A table referencing lexemes that fill_lexicon.sql doesn't know about must not
    lose its parent row: the foreign key fails the DELETE and nothing is changed."""
    conn.execute("CREATE TABLE notes (lexeme_id INTEGER REFERENCES lexemes (lexeme_id))")
    old = add_old_row(conn, "tambien")
    conn.execute("INSERT INTO notes VALUES (?)", (old,))
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        fill_lexicon(conn, ENTRIES)
    assert [r["lemma"] for r in conn.execute("SELECT lemma FROM lexemes")] == ["tambien"]


# --- Multi-word expressions -------------------------------------------------------------


def test_approved_expressions_become_entries_with_a_tatoeba_frequency(make_wiktionary):
    wiktionary = make_wiktionary([("casa", "noun", "house")])
    expression = [("sin embargo", [("sin embargo", "EXPR")]), ("embargo", [])]
    sentences = [
        sentence(1, [("casa", [CASA])] + expression, en="However, the house."),
        sentence(2, [("casa", [CASA])]),
        sentence(3, [("casa", [CASA]), ("de", [DE]), ("nada", [("de nada", "EXPR")])]),
    ]
    entries = build_entries(
        lambda: sentences,
        Counter({"casa": 10}),
        wiktionary,
        report=quiet,
        expressions={"sin embargo": "however, nevertheless"},  # "de nada" isn't approved
    )
    by_key = {(e.lemma, e.pos): e for e in entries}
    phrase = by_key["sin embargo", "EXPR"]
    assert phrase.definition_en == "however, nevertheless"
    # 1 occurrence in 7 Tatoeba tokens; subtitle counts don't cover phrases.
    assert phrase.frequency_per_million == round(1 / 7 * 1e6, 4)
    assert phrase.example and phrase.example.sentence_id == 1
    assert ("de nada", "EXPR") not in by_key
