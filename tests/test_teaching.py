"""Choosing a readable example when a word is taught (each step of the fallback order)."""

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from spanish_tutor.ingest.index_tatoeba import index_sentences
from spanish_tutor.ingest.tatoeba import AnalyzedSentence
from spanish_tutor.teaching import lesson
from spanish_tutor.vectorstore import open_store
from spanish_tutor.words import Lexeme

NADAR, RIO, PERRO, EL = ("nadar", "VERB"), ("río", "NOUN"), ("perro", "NOUN"), ("el", "DET")
STORED = "El perro nada en el río."
VOCAB = {
    STORED: {PERRO, NADAR, EL, RIO},
    "El perro nada.": {PERRO, NADAR, EL},
}


def vocab_of(text):
    return VOCAB[text]


@pytest.fixture
def nadar(conn):
    """nadar, with a stored Tatoeba example that also contains río."""
    conn.executemany(
        "INSERT INTO lexemes (lemma, pos, definition_en) VALUES (?, ?, ?)",
        [("perro", "NOUN", "dog"), ("el", "DET", "the"), ("río", "NOUN", "river")],
    )
    lexeme_id = conn.execute(
        """
        INSERT INTO lexemes (lemma, pos, definition_en, example_es, example_en,
                             example_source, example_author)
        VALUES ('nadar', 'VERB', 'to swim', ?, 'The dog swims in the river.', 'tatoeba:7', 'ana')
        """,
        (STORED,),
    ).lastrowid
    return Lexeme(lexeme_id, "nadar", "VERB")


@pytest.fixture
def store(tmp_path):
    store = open_store(tmp_path / "chroma", embeddings=DeterministicFakeEmbedding(size=32))
    tokens = [(lemma, [(lemma, pos)]) for lemma, pos in sorted(VOCAB["El perro nada."])]
    sentence = AnalyzedSentence(
        id=9, es="El perro nada.", en="The dog swims.", author="luis", tokens=tokens
    )
    index_sentences(store, [sentence], report=lambda _: None)
    return store


def test_readable_stored_example_is_used(conn, nadar):
    result = lesson(conn, nadar, {PERRO, EL, RIO}, vocab_of)
    assert result.definition_en == "to swim"
    assert (result.example.es, result.example.source, result.example.author) == (
        STORED,
        "tatoeba:7",
        "ana",
    )
    assert result.example.glosses == ()


def test_unreadable_stored_example_is_replaced_by_a_readable_search_hit(conn, nadar, store):
    result = lesson(conn, nadar, {PERRO, EL}, vocab_of, store=store)  # río unknown
    assert (result.example.es, result.example.en) == ("El perro nada.", "The dog swims.")
    assert (result.example.source, result.example.author) == ("tatoeba:9", "luis")


def test_without_a_readable_hit_the_stored_example_comes_with_glosses(conn, nadar):
    result = lesson(conn, nadar, {PERRO, EL}, vocab_of, store=None)
    assert result.example.es == STORED
    assert result.example.source == "tatoeba:7"  # credit is kept
    assert result.example.glosses == (("río", "river"),)


def test_word_with_no_stored_example_uses_the_sentence_where_it_was_met(conn):
    lexeme_id = conn.execute(
        "INSERT INTO lexemes (lemma, pos, definition_en) VALUES ('chapotear', 'VERB', 'to splash')"
    ).lastrowid
    met = "Los niños chapotean en el agua."
    result = lesson(conn, Lexeme(lexeme_id, "chapotear", "VERB"), set(), vocab_of, met_in=met)
    assert (result.example.es, result.example.en, result.example.source) == (met, None, None)


def test_word_with_no_example_anywhere_gets_definition_only(conn):
    lexeme_id = conn.execute(
        "INSERT INTO lexemes (lemma, pos, definition_en) VALUES ('chapotear', 'VERB', 'to splash')"
    ).lastrowid
    result = lesson(conn, Lexeme(lexeme_id, "chapotear", "VERB"), set(), vocab_of)
    assert result.example is None
    assert result.definition_en == "to splash"
    assert not result.model_written


def test_a_model_defined_word_is_marked_model_written(conn, monkeypatch):
    """A word resolve.py added: Claude's definition, and the real sentence it was met in."""
    lexeme_id = conn.execute(
        """
        INSERT INTO lexemes (lemma, pos, definition_en, definition_source, example_es,
                             example_en, example_source, example_en_source)
        VALUES ('chambear', 'VERB', 'to work (informal)', 'model:claude-opus-5-5',
                'Hay que chambear.', 'You have to work.', 'content:3', 'model:claude-opus-5-5')
        """
    ).lastrowid
    monkeypatch.setitem(VOCAB, "Hay que chambear.", {("chambear", "VERB")})
    result = lesson(conn, Lexeme(lexeme_id, "chambear", "VERB"), set(), vocab_of)
    assert result.model_written
    assert (result.example.es, result.example.source) == ("Hay que chambear.", "content:3")
