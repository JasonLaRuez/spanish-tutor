"""Topic vocabulary for pre-teaching, grounded in real sentences.

Before a topic conversation the tutor teaches a handful of words the learner will need
(roadmap Phase 2). The candidates come from data rather than from the model's guess:

1. Retrieve the Tatoeba sentences closest in meaning to the topic.
2. Count the content words in them that the learner doesn't know yet.
3. Rank by topic association: how much more often a word appears near the topic than its
   overall frequency predicts. Common words that turn up everywhere ("lleno") sink;
   topic words ("regar", "césped") rise.

Claude then chooses the most useful for talking about the topic, but only from these
candidates: it can rank, not invent. Measured on "el jardín" (2026-10-02), the top
candidates included planta, regar, jardinero, hierba, hoja, césped, plantar, florecer;
hierba and hoja were words Jason had to look up in his first conversation.

How deep to search (measured 2026-10-05, 8 topics, top 40 candidates labeled by hand as
on-topic or noise): the 300 nearest sentences left most topics with 5-15 candidates, and a
word needed only 3 chance appearances to get in (posponer for "el tiempo"). Deeper search
is cleaner, because real topic words pile up counts while coincidences fall back:

    sentences   noise in top 20   noise in top 40   on-topic words offered
    300         29%               29%                49
    1,000       18%               26%               212
    2,000       12%               22%               250

Broad topics stay noisy at any depth ("el trabajo": 16 of 40 on-topic), so Claude may
choose fewer words than asked for, and says why.

Practice words fill the gap (Jason, 2026-10-06). When fewer new words are chosen than
asked for, the rest come from on-topic words the learner recognizes but has never used:
the conversation is for production, and the reading and song skills grow recognition
much faster, so a topic can run out of new words long before it runs out of words to
practice. They come from the same search and ranking. Measured on the real word bank
(254 content words recognized, not produced; 8 topics): 14-26 such candidates per topic.
Those scoring above 0 were on-topic (calor and cielo for "el tiempo", almorzar, cenar and
desayuno for "la comida", tren and avión for "viajar"), those at or below 0 were noise
(ya, dejar, llevar, acabar), so practice candidates must score above 0: 6-20 per topic.
"""

import math
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from langchain_chroma import Chroma
from pydantic import BaseModel, Field

from spanish_tutor.lexicon import Analysis
from spanish_tutor.vectorstore import decode_vocab

CONTENT_POS = frozenset(["NOUN", "VERB", "ADJ", "ADV"])
MIN_OCCURRENCES = 3  # in the retrieved sentences: fewer is coincidence
WORDS_PER_SENTENCE = 7.3  # Tatoeba average (3.22M tokens / 442k sentences)
MIN_WORDS, MAX_WORDS, DEFAULT_WORDS = 2, 20, 5
SENTENCES = 2000  # nearest sentences searched for candidates (see above)
MIN_CANDIDATES = 25


def candidate_count(n: int) -> int:
    """How many candidates Claude chooses n words from: twice n, so it still has a choice."""
    return max(MIN_CANDIDATES, 2 * n)


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


def topic_pools(
    conn: sqlite3.Connection,
    store: Chroma,
    topic: str,
    recognized: set[Analysis],
    produced: set[Analysis],
    *,
    k: int = SENTENCES,
    limit: int = MIN_CANDIDATES,
) -> tuple[list[Candidate], list[Candidate]]:
    """From one search: (new, practice) content words associated with `topic`, best first.

    New words aren't recognized yet. Practice words are recognized but not produced, and
    must score above 0 (more frequent near the topic than overall; see the module note).
    """
    counts: Counter[Analysis] = Counter()
    for doc in store.similarity_search(topic, k=k):
        for word in decode_vocab(doc.metadata["vocab"]):
            if word[1] in CONTENT_POS:
                counts[word] += 1
    new, practice = [], []
    for (lemma, pos), n in counts.items():
        if n < MIN_OCCURRENCES or (lemma, pos) in produced:
            continue
        row = conn.execute(
            "SELECT frequency_per_million, definition_en FROM lexemes WHERE lemma = ? AND pos = ?",
            (lemma, pos),
        ).fetchone()
        if row is None or not row[0]:
            continue  # not in the lexicon, or no frequency evidence
        expected = k * WORDS_PER_SENTENCE * row[0] / 1e6
        candidate = Candidate(lemma, pos, row[1], n, n * math.log(n / expected))
        if (lemma, pos) not in recognized:
            new.append(candidate)
        elif candidate.score > 0:
            practice.append(candidate)
    for pool in (new, practice):
        pool.sort(key=lambda c: (-c.score, c.key))
    return new[:limit], practice[:limit]


def topic_candidates(
    conn: sqlite3.Connection,
    store: Chroma,
    topic: str,
    known: set[Analysis],
    *,
    k: int = SENTENCES,
    limit: int = MIN_CANDIDATES,
) -> list[Candidate]:
    """Unknown content words associated with `topic`, best first."""
    return topic_pools(conn, store, topic, known, set(), k=k, limit=limit)[0]


class TopicWords(BaseModel):
    """Claude's choice of words to pre-teach, and of known words to practice."""

    words: list[str] = Field(
        description="The chosen new words, most useful first, each exactly as listed "
        "(lemma|POS). Fewer than asked for if not enough candidates are really about the "
        "topic."
    )
    practice: list[str] = Field(
        description="Only if `words` has fewer than asked for: words from the second list "
        "(known, never used) to fill the gap, most useful first, each exactly as listed. "
        "Empty otherwise."
    )
    # Required but nullable, like TutorReply.note_en: the schema the API already accepts.
    fewer_because: str | None = Field(
        description="Only if you chose fewer words in all than asked for: one short sentence "
        "in English, addressed to the learner, saying why. Null otherwise."
    )


@dataclass(frozen=True)
class TopicChoice:
    words: list[Candidate]  # new words, to teach
    requested: int
    shortfall: str | None  # why fewer words than requested, for the learner; else None
    practice: list[Candidate] = field(default_factory=list)  # known words to practice using


def _listing(candidates: list[Candidate]) -> str:
    return "\n".join(f"- {c.key}: {c.definition_en or '(no definition)'}" for c in candidates)


def selection_prompt(
    topic: str, candidates: list[Candidate], n: int, practice: list[Candidate] | None = None
) -> str:
    prompt = (
        f"A Spanish learner is about to have a conversation about: {topic}. Before it starts, "
        f"they will learn up to {n} new words, so they have the vocabulary the topic needs. "
        f"From the candidates below (each with its English meaning), choose up to {n} that "
        "will be most useful for understanding and talking about this topic: words central "
        "to it and common in everyday speech. Choose fewer rather than include words that "
        "aren't really about the topic or only seem to appear by coincidence; if you do, say "
        "why in one short sentence. Return the words exactly as written (lemma|POS), most "
        "useful first.\n\n" + (_listing(candidates) or "(none)")
    )
    if practice:
        prompt += (
            "\n\nThe learner already understands the words below but has never used them "
            "in their own Spanish. If you chose fewer than "
            f"{n} new words, fill the remaining places from this list (in `practice`), with "
            "words really about the topic, so the learner can practice using them. Never "
            "choose one of these instead of a new word that fits.\n\n" + _listing(practice)
        )
    return prompt


def _valid(named: list[str], pool: list[Candidate], limit: int) -> list[Candidate]:
    """The named words that are in the pool, once each, up to `limit`. If words were named
    but none is in the pool, the pool's best `limit` instead."""
    by_key = {c.key: c for c in pool}
    valid = [by_key[w.strip()] for w in dict.fromkeys(named) if w.strip() in by_key][:limit]
    return pool[:limit] if named and not valid else valid


def choose_words(
    select: Callable[[str], TopicWords],
    topic: str,
    candidates: list[Candidate],
    n: int,
    practice: list[Candidate] | None = None,
) -> TopicChoice:
    """Up to n words Claude finds most useful, validated against the lists.

    New words come first (`candidates`); when fewer than n are chosen, the gap is filled
    from `practice` (known words the learner hasn't used yet). Anything not on its list is
    dropped, so every word is grounded in real sentences. If Claude named words from a
    list but none were on it, that list's top scores are used instead. Whenever fewer
    than n words result, the choice says why, for the learner.
    """
    practice = practice or []
    if not candidates and not practice:
        return TopicChoice([], n, f"No words about “{topic}” turned up that you don't know yet.")
    reply = select(selection_prompt(topic, candidates, n, practice))
    words = _valid(reply.words, candidates, n)
    filled = _valid(reply.practice, practice, n - len(words)) if len(words) < n else []
    why = shortfall(topic, n, len(candidates), len(words), reply, len(practice), len(filled))
    return TopicChoice(words, n, why, filled)


def shortfall(
    topic: str,
    requested: int,
    available: int,
    chosen: int,
    reply: TopicWords,
    practice_available: int = 0,
    practice_chosen: int = 0,
) -> str | None:
    """Why fewer words than requested were chosen, for the learner; None if none are missing."""
    if chosen + practice_chosen >= requested:
        return None
    found = f"Only {available} words about “{topic}” turned up that you don't know yet"
    if practice_available:
        found += f", and {practice_available} you know but haven't used yet"
    found += "."
    if chosen == available and practice_chosen == practice_available:
        return found
    why = (reply.fewer_because or "").strip() or "The rest weren't really about the topic."
    return f"{found} {why}" if available + practice_available < requested else why


def word_count(answer: str) -> int:
    """The learner's answer to "how many new words?", clamped to 2-20 (Enter = 5)."""
    try:
        n = int(answer.strip())
    except ValueError:
        return DEFAULT_WORDS
    return max(MIN_WORDS, min(MAX_WORDS, n))
