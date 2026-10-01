"""The teach step shared by every skill: a new word's definition and a readable example.

An example is only useful if the learner can read everything in it except the word being
taught. The lexicon stores one example per word, chosen for a generic learner who knows
the 2,000 most frequent words; for this learner it is fully readable about 61% of the
time (measured 2026-10-01). So the example is re-chosen at teach time:

1. the stored example, if every other word in it is known;
2. otherwise the most similar Tatoeba sentence that is readable (vectorstore.find_example);
3. otherwise the stored example anyway, with English glosses for its other unknown words
   (one level deep: glossed words are not taught, so a lesson never snowballs);
4. otherwise, for words with no stored example (the rare tail), the sentence where the
   learner met the word.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from langchain_chroma import Chroma

from spanish_tutor.lexicon import Analysis
from spanish_tutor.vectorstore import find_example
from spanish_tutor.words import Lexeme


@dataclass(frozen=True)
class Example:
    es: str
    en: str | None
    source: str | None  # "tatoeba:<id>"; None for the sentence where the word was met
    author: str | None  # Tatoeba username, for attribution
    glosses: tuple[tuple[str, str], ...] = ()  # (lemma, English) for other unknown words


@dataclass(frozen=True)
class Lesson:
    lexeme_id: int
    lemma: str
    pos: str
    definition_en: str | None
    example: Example | None


def lesson(
    conn: sqlite3.Connection,
    lexeme: Lexeme,
    known: set[Analysis],
    vocab_of: Callable[[str], set[Analysis]],
    store: Chroma | None = None,
    met_in: str | None = None,
) -> Lesson:
    """Build the lesson for `lexeme`, choosing an example `known` makes readable.

    `vocab_of` maps text to its (lemma, pos) set (lexicon.analyze in the app). `store`
    is the Tatoeba vector store; `met_in` is the text where the learner met the word.
    """
    row = conn.execute(
        """
        SELECT definition_en, example_es, example_en, example_source, example_author
        FROM lexemes WHERE lexeme_id = ?
        """,
        (lexeme.lexeme_id,),
    ).fetchone()
    target = lexeme.analysis
    stored = None
    if row["example_es"]:
        stored = Example(
            row["example_es"], row["example_en"], row["example_source"], row["example_author"]
        )
    example = stored
    if stored and (others := vocab_of(stored.es) - known - {target}):
        hit = None
        if store is not None:
            query = f"{lexeme.lemma}: {row['definition_en'] or ''}"
            hit = find_example(store, target, known, query)
        if hit:
            example = Example(hit.es, hit.en, f"tatoeba:{hit.sentence_id}", hit.author)
        else:
            example = Example(
                stored.es, stored.en, stored.source, stored.author, glosses(conn, others)
            )
    elif stored is None and met_in:
        example = Example(met_in, None, None, None)
    return Lesson(lexeme.lexeme_id, lexeme.lemma, lexeme.pos, row["definition_en"], example)


def glosses(conn: sqlite3.Connection, words: set[Analysis]) -> tuple[tuple[str, str], ...]:
    """English definitions for words, where the lexicon has one, in alphabetical order."""
    found = []
    for lemma, pos in sorted(words):
        row = conn.execute(
            "SELECT definition_en FROM lexemes WHERE lemma = ? AND pos = ?", (lemma, pos)
        ).fetchone()
        if row and row[0]:
            found.append((lemma, row[0]))
    return tuple(found)
