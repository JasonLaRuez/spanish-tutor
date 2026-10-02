"""Topic vocabulary for pre-teaching, grounded in real sentences.

Before a topic conversation the tutor teaches a handful of words the learner will need
(roadmap Phase 2). The candidates come from data rather than from the model's guess:

1. Retrieve the Tatoeba sentences closest in meaning to the topic.
2. Count the content words in them that the learner doesn't know yet.
3. Rank by topic association: how much more often a word appears near the topic than its
   overall frequency predicts. Common words that turn up everywhere ("lleno") sink;
   topic words ("regar", "césped") rise.

Claude then chooses the most useful few for talking about the topic, but only from these
candidates: it can rank, not invent. Measured on "el jardín" (2026-10-02), the top
candidates included planta, regar, jardinero, hierba, hoja, césped, plantar, florecer;
hierba and hoja were words Jason had to look up in his first conversation.
"""

import math
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from langchain_chroma import Chroma
from pydantic import BaseModel, Field

from spanish_tutor.lexicon import Analysis
from spanish_tutor.vectorstore import decode_vocab

CONTENT_POS = frozenset(["NOUN", "VERB", "ADJ", "ADV"])
MIN_OCCURRENCES = 3  # in the retrieved sentences: fewer is coincidence
WORDS_PER_SENTENCE = 7.3  # Tatoeba average (3.22M tokens / 442k sentences)
MIN_WORDS, MAX_WORDS, DEFAULT_WORDS = 2, 10, 5


@dataclass(frozen=True)
class Candidate:
    lemma: str
    pos: str
    definition_en: str | None
    occurrences: int  # in the retrieved sentences
    score: float  # topic association

    @property
    def key(self) -> str:
        return f"{self.lemma}|{self.pos}"

    @property
    def analysis(self) -> Analysis:
        return self.lemma, self.pos


def topic_candidates(
    conn: sqlite3.Connection,
    store: Chroma,
    topic: str,
    known: set[Analysis],
    *,
    k: int = 300,
    limit: int = 25,
) -> list[Candidate]:
    """Unknown content words associated with `topic`, best first."""
    counts: Counter[Analysis] = Counter()
    for doc in store.similarity_search(topic, k=k):
        for word in decode_vocab(doc.metadata["vocab"]) - known:
            if word[1] in CONTENT_POS:
                counts[word] += 1
    candidates = []
    for (lemma, pos), n in counts.items():
        if n < MIN_OCCURRENCES:
            continue
        row = conn.execute(
            "SELECT frequency_per_million, definition_en FROM lexemes WHERE lemma = ? AND pos = ?",
            (lemma, pos),
        ).fetchone()
        if row is None or not row[0]:
            continue  # not in the lexicon, or no frequency evidence
        expected = k * WORDS_PER_SENTENCE * row[0] / 1e6
        candidates.append(Candidate(lemma, pos, row[1], n, n * math.log(n / expected)))
    candidates.sort(key=lambda c: (-c.score, c.key))
    return candidates[:limit]


class TopicWords(BaseModel):
    """Claude's choice of words to pre-teach."""

    words: list[str] = Field(
        description="The chosen words, most useful first, each exactly as listed (lemma|POS)."
    )


def selection_prompt(topic: str, candidates: list[Candidate], n: int) -> str:
    listing = "\n".join(f"- {c.key}: {c.definition_en or '(no definition)'}" for c in candidates)
    return (
        f"A Spanish learner is about to have a conversation about: {topic}. Before it starts, "
        f"they will learn {n} new words. From the candidates below (each with its English "
        f"meaning), choose the {n} that will be most useful for understanding and talking "
        "about this topic: words central to it and common in everyday speech. Skip words "
        "that only seem to appear by coincidence. Return them exactly as written "
        "(lemma|POS), most useful first.\n\n" + listing
    )


def choose_words(
    select: Callable[[str], TopicWords], topic: str, candidates: list[Candidate], n: int
) -> list[Candidate]:
    """The n candidates Claude finds most useful, validated against the list.

    Anything not on the list is dropped, so every taught word is grounded in real
    sentences. If fewer than two valid choices remain, the top n by score are used.
    """
    by_key = {c.key: c for c in candidates}
    chosen = select(selection_prompt(topic, candidates, n)).words
    valid = [by_key[w.strip()] for w in dict.fromkeys(chosen) if w.strip() in by_key][:n]
    if len(valid) < min(MIN_WORDS, len(candidates)):
        return candidates[:n]
    return valid


def word_count(answer: str) -> int:
    """The learner's answer to "how many new words?", clamped to 2-10 (Enter = 5)."""
    try:
        n = int(answer.strip())
    except ValueError:
        return DEFAULT_WORDS
    return max(MIN_WORDS, min(MAX_WORDS, n))
