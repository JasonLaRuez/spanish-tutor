"""Choosing topic words to pre-teach: grounded candidates, constrained selection."""

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from spanish_tutor.ingest.index_tatoeba import index_sentences
from spanish_tutor.ingest.tatoeba import AnalyzedSentence
from spanish_tutor.topics import (
    Candidate,
    TopicWords,
    candidate_count,
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


def picked(*words, why=None):
    return lambda prompt: TopicWords(words=list(words), fewer_because=why)


def test_claude_chooses_only_from_the_candidates():
    pool = candidates("planta", "hoja", "césped", "riego")
    select = picked("hoja|NOUN", "tulipán|NOUN", " césped|NOUN ", "hoja|NOUN")
    choice = choose_words(select, "el jardín", pool, 2)
    assert [c.lemma for c in choice.words] == ["hoja", "césped"]  # invented and repeated dropped
    assert choice.shortfall is None


def test_choices_none_of_which_are_candidates_fall_back_to_the_best_scores():
    pool = candidates("planta", "hoja", "césped")
    choice = choose_words(picked("rosa|NOUN"), "el jardín", pool, 2)
    assert [c.lemma for c in choice.words] == ["planta", "hoja"]


def test_claude_may_choose_fewer_and_its_reason_is_kept():
    pool = candidates(*[f"w{i}" for i in range(30)])
    select = picked("w0|NOUN", "w1|NOUN", why="The rest are too general.")
    choice = choose_words(select, "x", pool, 5)
    assert ([c.lemma for c in choice.words], choice.requested) == (["w0", "w1"], 5)
    assert choice.shortfall == "The rest are too general."


def test_a_shortfall_without_a_reason_gets_a_default_one():
    pool = candidates(*[f"w{i}" for i in range(30)])
    choice = choose_words(picked("w0|NOUN"), "x", pool, 5)
    assert choice.shortfall == "The rest weren't really about the topic."


def test_too_few_candidates_is_explained_by_their_number():
    pool = candidates("planta", "hoja", "césped")
    select = picked("planta|NOUN", "hoja|NOUN", "césped|NOUN")
    choice = choose_words(select, "el jardín", pool, 10)
    assert len(choice.words) == 3
    assert choice.shortfall == "Only 3 words about “el jardín” turned up that you don't know yet."


def test_claude_may_choose_none_when_nothing_fits():
    pool = candidates("planta", "hoja")
    choice = choose_words(picked(why="None of these are about cooking."), "la cocina", pool, 5)
    assert choice.words == []
    assert choice.shortfall == (
        "Only 2 words about “la cocina” turned up that you don't know yet. "
        "None of these are about cooking."
    )


def test_no_candidates_means_no_request_to_claude():
    def select(prompt):
        raise AssertionError("nothing to choose from")

    choice = choose_words(select, "el jardín", [], 5)
    assert choice.words == [] and "No words about “el jardín”" in choice.shortfall


def test_selection_prompt_lists_every_candidate_and_allows_fewer():
    prompt = selection_prompt("el jardín", candidates("planta", "hoja"), 2)
    assert "- planta|NOUN: PLANTA" in prompt and "- hoja|NOUN: HOJA" in prompt
    assert "el jardín" in prompt and "choose up to 2" in prompt
    assert "Choose fewer rather than include words that aren't really about the topic" in prompt


@pytest.mark.parametrize("n, count", [(2, 25), (5, 25), (12, 25), (13, 26), (20, 40)])
def test_claude_chooses_from_at_least_twice_as_many_candidates(n, count):
    assert candidate_count(n) == count


@pytest.mark.parametrize(
    "answer, n", [("", 5), ("3", 3), ("20", 20), ("25", 20), ("1", 2), ("muchas", 5)]
)
def test_word_count_is_clamped_to_two_through_twenty(answer, n):
    assert word_count(answer) == n
