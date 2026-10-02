"""Build the general lexicon: every Tatoeba word that has a Wiktionary entry.

    uv run python -m spanish_tutor.ingest.build_lexicon

The lexicon is independent of any learner: anyone who runs the pipeline gets the same
standard database of words, each with a frequency, an English definition, and an
attributed example sentence. The learner's data (word_events, word_bank) only refers
to it.

Rebuilds are additive. Rows are never deleted, so lexeme_ids, and the learner events
that reference them, stay valid. Frequencies are recomputed each time; definitions and
examples only fill empty fields (see sql/fill_lexicon.sql). The database is backed up
before it is changed.
"""

import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import NamedTuple

from spanish_tutor import db
from spanish_tutor.config import DB_PATH, SQL_DIR
from spanish_tutor.ingest import subtlex
from spanish_tutor.ingest.download import RAW_DIR, WIKTIONARY_FILE
from spanish_tutor.ingest.tatoeba import (
    ANALYZED_PATH,
    AnalyzedSentence,
    TaggerMismatch,
    read_analyzed,
    require_current,
)
from spanish_tutor.ingest.wiktionary import Wiktionary
from spanish_tutor.lexicon import Analysis

# Example sentences are chosen to be readable by someone who knows this many of the most
# frequent words: a learner-independent stand-in for "comprehensible", about the size of
# a learner's dictionary defining vocabulary. Teaching re-chooses examples per learner.
BASIC_VOCABULARY_SIZE = 2000

# Example sentence length, in word tokens.
MIN_LENGTH, IDEAL_LENGTH, MAX_LENGTH = 4, 6, 10


# --- Frequency -------------------------------------------------------------------------


def form_analysis_counts(
    sentences: Iterable[AnalyzedSentence],
) -> tuple[Counter[str], dict[str, Counter[Analysis]]]:
    """How often each surface form occurs, and how often with each (lemma, pos)."""
    occurrences: Counter[str] = Counter()
    with_analysis: dict[str, Counter[Analysis]] = defaultdict(Counter)
    for sentence in sentences:
        for form, analyses in sentence.tokens:
            occurrences[form] += 1
            with_analysis[form].update(analyses)
    return occurrences, with_analysis


def lemma_frequencies(
    form_counts: Counter[str],
    occurrences: Counter[str],
    with_analysis: dict[str, Counter[Analysis]],
) -> tuple[Counter[Analysis], int]:
    """Split each form's subtitle count across its analyses, weighted by Tatoeba usage.

    A contraction's analyses each get the full count ("del" is always both "de" and "el").
    Forms that never occur in Tatoeba can't be disambiguated; their count is returned as
    the second value so the caller can report how much frequency mass was dropped.
    """
    frequencies: Counter[Analysis] = Counter()
    dropped = 0
    for form, count in form_counts.items():
        seen = occurrences.get(form, 0)
        if not seen:
            dropped += count
            continue
        for analysis, n in with_analysis[form].items():
            frequencies[analysis] += count * n / seen
    return frequencies, dropped


# --- Examples --------------------------------------------------------------------------


@dataclass(frozen=True)
class Example:
    sentence_id: int
    es: str
    en: str
    author: str | None


def pick_examples(
    sentences: Iterable[AnalyzedSentence], wanted: set[Analysis], known: set[Analysis]
) -> dict[Analysis, Example]:
    """Best translated example sentence for each wanted (lemma, pos).

    Preference: fewest *other* words outside `known` (comprehensible input: ideally a
    sentence the reader can fully understand), then a useful length (fragments like
    "Soy." show no usage; long sentences bury the word), then closest to IDEAL_LENGTH
    words, then lowest id.
    """
    best: dict[Analysis, tuple[tuple[int, int, int, int], Example]] = {}
    for sentence in sentences:
        if not sentence.en:
            continue
        vocab = {a for _, analyses in sentence.tokens for a in analyses}
        present = vocab & wanted
        if not present:
            continue
        unknown = vocab - known
        length = len(sentence.tokens)
        outside_range = 0 if MIN_LENGTH <= length <= MAX_LENGTH else 1
        for analysis in present:
            key = (
                len(unknown - {analysis}),
                outside_range,
                abs(length - IDEAL_LENGTH),
                sentence.id,
            )
            if analysis not in best or key < best[analysis][0]:
                example = Example(sentence.id, sentence.es, sentence.en, sentence.author)
                best[analysis] = (key, example)
    return {analysis: example for analysis, (_, example) in best.items()}


# --- Building and loading --------------------------------------------------------------


@dataclass(frozen=True)
class LexiconEntry:
    lemma: str
    pos: str
    frequency_per_million: float | None  # None: no subtitle evidence for this word
    definition_en: str | None
    example: Example | None

    def row(self) -> tuple:
        ex = self.example
        return (
            self.lemma,
            self.pos,
            self.frequency_per_million,
            self.definition_en,
            "wiktionary" if self.definition_en else None,
            ex.es if ex else None,
            ex.en if ex else None,
            f"tatoeba:{ex.sentence_id}" if ex else None,
            ex.author if ex else None,
        )


def build_entries(
    sentences: Callable[[], Iterable[AnalyzedSentence]],
    form_counts: Counter[str],
    wiktionary: Wiktionary,
    report: Callable[[str], None] = print,
) -> list[LexiconEntry]:
    """One entry per (lemma, pos) in the corpus that Wiktionary recognizes.

    `sentences` is called twice (frequency pass, example pass) so the corpus can be
    streamed from disk rather than held in memory.
    """
    occurrences, with_analysis = form_analysis_counts(sentences())
    frequencies, dropped = lemma_frequencies(form_counts, occurrences, with_analysis)
    total = sum(form_counts.values())
    report(f"  {dropped / total:.1%} of subtitle tokens are forms absent from Tatoeba")

    in_corpus = {a for counts in with_analysis.values() for a in counts}
    words = {a for a in in_corpus if a in wiktionary}
    report(f"  {len(in_corpus):,} distinct (lemma, pos) in Tatoeba, {len(words):,} in Wiktionary")

    ranked = sorted(words, key=lambda a: frequencies.get(a, 0), reverse=True)
    basic = set(ranked[:BASIC_VOCABULARY_SIZE])
    examples = pick_examples(sentences(), words, known=basic)

    return [
        LexiconEntry(
            lemma=lemma,
            pos=pos,
            frequency_per_million=(
                round(frequencies[lemma, pos] / total * 1e6, 4)
                if (lemma, pos) in frequencies
                else None
            ),
            definition_en=wiktionary.definition(lemma, pos),
            example=examples.get((lemma, pos)),
        )
        for lemma, pos in ranked
    ]


STAGING_TABLE = """
CREATE TEMP TABLE lexicon_staging (
    lemma TEXT NOT NULL, pos TEXT NOT NULL, frequency_per_million REAL,
    definition_en TEXT, definition_source TEXT, example_es TEXT, example_en TEXT,
    example_source TEXT, example_author TEXT
)
"""


class FillCounts(NamedTuple):
    added: int  # new words
    updated: int  # words already in lexemes, refreshed
    removed: int  # words the build no longer produces, with nothing referencing them
    cleared: int  # words the build no longer produces, kept for their history; frequency cleared


def fill_lexicon(conn: sqlite3.Connection, entries: Iterable[LexiconEntry]) -> FillCounts:
    """Stage the entries and merge them into lexemes with sql/fill_lexicon.sql.

    Runs in one transaction: all or nothing. The file's statements run one at a time
    (executescript would commit first) so each can report how many rows it changed.
    """
    before = conn.execute("SELECT COUNT(*) FROM lexemes").fetchone()[0]
    with conn:
        conn.execute("DROP TABLE IF EXISTS temp.lexicon_staging")
        conn.execute(STAGING_TABLE)
        conn.executemany(
            "INSERT INTO lexicon_staging VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry.row() for entry in entries),
        )
        staged = conn.execute("SELECT COUNT(*) FROM lexicon_staging").fetchone()[0]
        merge, delete, clear = db.statements(
            (SQL_DIR / "fill_lexicon.sql").read_text(encoding="utf-8")
        )
        conn.execute(merge)
        added = conn.execute("SELECT COUNT(*) FROM lexemes").fetchone()[0] - before
        removed = conn.execute(delete).rowcount
        cleared = conn.execute(clear).rowcount
        conn.execute("DROP TABLE lexicon_staging")
    return FillCounts(added, staged - added, removed, cleared)


def main() -> None:
    paths = [RAW_DIR / WIKTIONARY_FILE, RAW_DIR / "SUBTLEX-ESP.xlsx", ANALYZED_PATH]
    if missing := [p.name for p in paths if not p.exists()]:
        sys.exit(
            f"Missing {', '.join(missing)}. Run `uv run python -m spanish_tutor.ingest.download` "
            "and `uv run python -m spanish_tutor.ingest.tatoeba` first."
        )

    try:
        require_current(ANALYZED_PATH)
    except TaggerMismatch as error:
        sys.exit(str(error))
    print("building entries ...")
    entries = build_entries(
        lambda: read_analyzed(ANALYZED_PATH),
        subtlex.load_counts(paths[1]),
        Wiktionary(paths[0]),
    )
    with_example = sum(1 for e in entries if e.example)
    print(f"  {len(entries):,} entries, {with_example:,} with an example sentence")

    if backup := db.backup(DB_PATH):
        print(f"backed up the database to {backup.name}")
    conn = db.connect(DB_PATH)
    try:
        db.init_schema(conn)
        counts = fill_lexicon(conn, entries)
        total = conn.execute("SELECT COUNT(*) FROM lexemes").fetchone()[0]
        print(
            f"lexemes: {counts.added:,} added, {counts.updated:,} updated, "
            f"{counts.removed:,} removed (no longer built, unreferenced), "
            f"{counts.cleared:,} with frequency cleared (no longer built, kept for history); "
            f"{total:,} in total"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
