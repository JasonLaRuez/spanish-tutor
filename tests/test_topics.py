"""Choosing topic words to pre-teach: grounded candidates, constrained selection."""

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from spanish_tutor.ingest.index_tatoeba import index_sentences
from spanish_tutor.ingest.tatoeba import AnalyzedSentence
from spanish_tutor.topics import (
    Candidate,
    TopicWords,
    choose_words,
    selection_prompt,
    topic_candidates,
    word_count,
)
from spanish_tutor.vectorstore import open_store

PLANTA, REGAR, LLENO, CASA = (
    ("planta", "NOUN"),
    ("regar", "VERB"),
    ("lleno", "ADJ"),
    ("casa", "NOUN"),
)
EL, RARO = ("el", "DET"), ("raro", "ADJ")


def sentence(id, vocab):
    tokens = [(lemma, [(lemma, pos)]) for lemma, pos in vocab]
    return AnalyzedSentence(id=id, es=f"frase {id}", en="sentence", author=None, tokens=tokens)


@pytest.fixture
def lexicon(conn):
    """Frequencies per million: lleno is common everywhere; planta and regar are not."""
    conn.executemany(
        "INSERT INTO lexemes (lemma, pos, frequency_per_million, definition_en) VALUES (?, ?, ?, ?)",
        [
            ("planta", "NOUN", 40.0, "plant"),
            ("regar", "VERB", 10.0, "to water"),
            ("lleno", "ADJ", 900.0, "full"),
            ("casa", "NOUN", 800.0, "house"),
            ("el", "DET", 50000.0, "the"),
            ("raro", "ADJ", 60.0, "strange"),
        ],
    )
    return conn


@pytest.fixture
def store(tmp_path):
    store = open_store(tmp_path / "chroma", embeddings=DeterministicFakeEmbedding(size=32))
    corpus = [sentence(i, [EL, PLANTA, REGAR]) for i in range(5)]
    corpus += [sentence(10 + i, [EL, LLENO, CASA]) for i in range(4)]
    corpus += [sentence(20, [RARO])]  # once: coincidence
    index_sentences(store, corpus, report=lambda _: None)
    return store


def test_candidates_are_unknown_content_words_seen_often_enough(lexicon, store):
    found = topic_candidates(lexicon, store, "el jardín", known={CASA}, k=10)
    keys = {c.key for c in found}
    assert keys == {
        "planta|NOUN",
        "regar|VERB",
        "lleno|ADJ",
    }  # not el (DET), casa (known), raro (once)


def test_topic_words_outrank_words_common_everywhere(lexicon, store):
    found = topic_candidates(lexicon, store, "el jardín", known=set(), k=10)
    order = [c.lemma for c in found]
    assert order.index("regar") < order.index("lleno")
    assert order.index("planta") < order.index("lleno")


def candidates(*lemmas):
    return [Candidate(lemma, "NOUN", lemma.upper(), 5, 10.0 - i) for i, lemma in enumerate(lemmas)]


def test_claude_chooses_only_from_the_candidates():
    pool = candidates("planta", "hoja", "césped", "riego")
    choice = TopicWords(words=["hoja|NOUN", "tulipán|NOUN", " césped|NOUN ", "hoja|NOUN"])
    chosen = choose_words(lambda prompt: choice, "el jardín", pool, 3)
    assert [c.lemma for c in chosen] == ["hoja", "césped"]  # invented and repeated words dropped


def test_too_few_valid_choices_fall_back_to_the_best_scores():
    pool = candidates("planta", "hoja", "césped")
    chosen = choose_words(lambda prompt: TopicWords(words=["rosa|NOUN"]), "el jardín", pool, 2)
    assert [c.lemma for c in chosen] == ["planta", "hoja"]


def test_selection_prompt_lists_every_candidate_with_its_meaning():
    prompt = selection_prompt("el jardín", candidates("planta", "hoja"), 2)
    assert "- planta|NOUN: PLANTA" in prompt and "- hoja|NOUN: HOJA" in prompt
    assert "el jardín" in prompt and "choose the 2" in prompt


@pytest.mark.parametrize(
    "answer, n", [("", 5), ("3", 3), ("10", 10), ("25", 10), ("1", 2), ("muchas", 5)]
)
def test_word_count_is_clamped_to_two_through_ten(answer, n):
    assert word_count(answer) == n
