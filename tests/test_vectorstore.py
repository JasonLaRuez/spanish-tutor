"""Sentence indexing and vocabulary-constrained search, with fake embeddings (fast)."""

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from spanish_tutor.ingest.index_tatoeba import index_sentences
from spanish_tutor.ingest.tatoeba import AnalyzedSentence
from spanish_tutor.vectorstore import (
    EmbeddingModelMismatch,
    decode_vocab,
    encode_vocab,
    open_store,
    search_sentences,
)

CASA, GATO, COMER, RARO = ("casa", "NOUN"), ("gato", "NOUN"), ("comer", "VERB"), ("raro", "ADJ")


def sentence(id, es, vocab, en="translation", author="someone"):
    tokens = [(lemma, [(lemma, pos)]) for lemma, pos in vocab]
    return AnalyzedSentence(id=id, es=es, en=en, author=author, tokens=tokens)


CORPUS = [
    sentence(1, "El gato come en casa.", [GATO, COMER, CASA]),
    sentence(2, "Un gato raro.", [GATO, RARO], author=None),
    sentence(3, "Sin traducción.", [CASA], en=None),
    sentence(4, "Mi casa.", [CASA]),
]


@pytest.fixture
def store(tmp_path):
    return open_store(tmp_path / "chroma", embeddings=DeterministicFakeEmbedding(size=32))


def quiet(_):
    pass


def test_vocab_encoding_round_trips():
    vocab = {CASA, ("sin embargo", "EXPR"), ("él", "PRON")}
    assert decode_vocab(encode_vocab(vocab)) == vocab


def test_index_stores_translated_sentences_with_metadata(store):
    added, skipped = index_sentences(store, CORPUS, report=quiet)

    assert (added, skipped) == (3, 0)  # sentence 3 has no translation
    stored = store.get(ids=["tatoeba:1", "tatoeba:2", "tatoeba:3"])
    by_id = dict(zip(stored["ids"], stored["metadatas"], strict=True))
    assert set(by_id) == {"tatoeba:1", "tatoeba:2"}
    assert by_id["tatoeba:1"]["author"] == "someone"
    assert decode_vocab(by_id["tatoeba:1"]["vocab"]) == {GATO, COMER, CASA}
    assert "author" not in by_id["tatoeba:2"]


def test_reindexing_adds_nothing(store):
    index_sentences(store, CORPUS, report=quiet)
    assert index_sentences(store, CORPUS, report=quiet) == (0, 3)


def test_interrupted_run_resumes(store):
    index_sentences(store, CORPUS[:2], report=quiet)
    assert index_sentences(store, CORPUS, batch_size=2, report=quiet) == (1, 2)


def test_search_keeps_only_comprehensible_sentences(store):
    index_sentences(store, CORPUS, report=quiet)
    known = {GATO, COMER, CASA}

    hits = search_sentences(store, "Un gato raro.", known, k=5, max_unknown=0)
    assert {h.sentence_id for h in hits} == {1, 4}  # sentence 2 contains "raro"


def test_search_allows_i_plus_one_and_reports_the_new_word(store):
    index_sentences(store, CORPUS, report=quiet)
    known = {GATO, COMER, CASA}

    hits = search_sentences(store, "Un gato raro.", known, k=5, max_unknown=1)
    assert hits[0].sentence_id == 2  # the exact text is the most similar
    assert hits[0].unknown == {RARO}
    assert hits[0].author is None


def test_search_returns_at_most_k(store):
    index_sentences(store, CORPUS, report=quiet)
    assert len(search_sentences(store, "casa", {GATO, COMER, CASA, RARO}, k=2)) == 2


def test_store_refuses_a_different_embedding_model(tmp_path):
    path, fake = tmp_path / "chroma", DeterministicFakeEmbedding(size=32)
    open_store(path, embeddings=fake, model_id="model-a", model_revision="1")
    open_store(path, embeddings=fake, model_id="model-a", model_revision="1")  # same: fine
    with pytest.raises(EmbeddingModelMismatch):
        open_store(path, embeddings=fake, model_id="model-b", model_revision="1")
