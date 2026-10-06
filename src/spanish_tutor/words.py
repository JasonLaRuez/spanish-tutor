"""Word-level word-bank operations shared by every skill: finding a word's lexeme, and
logging what happened to it.

Text becomes (lemma, pos) pairs in lexicon.py. This module maps those pairs to rows in
`lexemes`, tolerating the most common learner slip (missing accents), adding real words
the general lexicon lacks from Wiktionary, and rejecting non-words so a misspelling is
never logged as vocabulary.
"""

import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from spanish_tutor.ingest.wiktionary import Wiktionary
from spanish_tutor.lexicon import Analysis

# Placeholder grades (0-5, SM-2 style) until Phase 6 replaces the familiarity formula.
# `seen` and `taught` events carry no grade: neither is evidence of recall.
GRADE_USED = 4  # the learner used the word, and it wasn't flagged as misused
GRADE_MISUSED = 2  # used, but wrongly (ser for estar, wrong agreement or tense)
GRADE_LOOKED_UP = 1  # the learner had to ask what a known word means

# Acute accents and the diaeresis. ñ is a separate letter (año, ano), so it is kept.
_STRIP_ACCENTS = str.maketrans("áéíóúü", "aeiouu")


def fold_accents(text: str) -> str:
    """Remove accents from normalized (NFC, lowercase) text: jardín -> jardin."""
    return text.translate(_STRIP_ACCENTS)


@dataclass(frozen=True)
class Lexeme:
    lexeme_id: int
    lemma: str
    pos: str

    @property
    def analysis(self) -> Analysis:
        return self.lemma, self.pos


def dictionary_definition(
    wiktionary: Wiktionary | None,
    expressions: Mapping[str, str] | None,
    analysis: Analysis,
) -> str | None | bool:
    """Whether (lemma, pos) is a real word: its definition if it is, False if not.

    Words come from Wiktionary. Expressions come from the approved list (phrase ->
    reviewed definition, ingest/expressions.py), since Wiktionary is keyed by single-word
    parts of speech and has no EXPR entries. A word Wiktionary knows without a usable
    definition gives None, which is still a word.
    """
    lemma, pos = analysis
    if pos == "EXPR":
        return (expressions or {}).get(lemma, False)
    if wiktionary is None or analysis not in wiktionary:
        return False
    return wiktionary.definition(lemma, pos)


def ensure_lexeme(
    conn: sqlite3.Connection,
    wiktionary: Wiktionary | None,
    lemma: str,
    pos: str,
    expressions: Mapping[str, str] | None = None,
) -> int | None:
    """The lexeme_id for (lemma, pos), adding the word from Wiktionary if it's missing.

    Approved expressions the lexicon lacks (those never found in Tatoeba) are added from
    `expressions`. Returns None, writing nothing, for anything neither knows: misspellings
    ("sabo") and tagger inventions must never enter the word bank. New rows have no
    frequency or example; like the lexicon build, this only ever adds rows.
    """
    row = conn.execute(
        "SELECT lexeme_id FROM lexemes WHERE lemma = ? AND pos = ?", (lemma, pos)
    ).fetchone()
    if row:
        return row[0]
    definition = dictionary_definition(wiktionary, expressions, (lemma, pos))
    if definition is False:
        return None
    return conn.execute(
        """
        INSERT INTO lexemes (lemma, pos, definition_en, definition_source)
        VALUES (?, ?, ?, 'wiktionary')
        """,
        (lemma, pos, definition),
    ).lastrowid


class LexiconIndex:
    """Resolves (lemma, pos) analyses to lexemes, in memory (the lexicon is ~26k rows).

    Resolution order:
    1. An exact match.
    2. The same word with accents restored, when exactly one lexeme fits: a learner who
       types "jardin" means "jardín". Ambiguous cases are left alone ("si" could be si or
       sí), and so is any spelling that is a word in its own right ("esta"), because
       resolution only reaches this step when there is no exact match.
    3. A word Wiktionary knows (or an approved expression), added to `lexemes`
       (ensure_lexeme).
    Anything else is not a word, and resolves to None.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        wiktionary: Wiktionary | None = None,
        expressions: Mapping[str, str] | None = None,
    ):
        self.conn = conn
        self.wiktionary = wiktionary
        self.expressions = expressions
        self.ids: dict[Analysis, int] = {}
        self.by_folded: dict[Analysis, list[str]] = defaultdict(list)
        # Headword -> its lexemes, most frequent first (for looking up a typed word).
        self.headwords: dict[str, list[Lexeme]] = defaultdict(list)
        rows = conn.execute(
            """
            SELECT lexeme_id, lemma, pos FROM lexemes
            ORDER BY frequency_per_million IS NULL, frequency_per_million DESC, lexeme_id
            """
        )
        for lexeme_id, lemma, pos in rows:
            self.add(lexeme_id, lemma, pos)

    def add(self, lexeme_id: int, lemma: str, pos: str) -> None:
        """Index a lexeme added to the table after this index was built."""
        self.ids[lemma, pos] = lexeme_id
        self.by_folded[fold_accents(lemma), pos].append(lemma)
        self.headwords[fold_accents(lemma)].append(Lexeme(lexeme_id, lemma, pos))

    def headword(self, word: str) -> Lexeme | None:
        """The lexeme a dictionary form names, for a word typed on its own.

        A lone word gives the tagger no context (spaCy tags a bare "perro" as a proper
        noun), so a typed word is matched against headwords directly: exact spelling
        first, then the same word without accents; the most frequent POS wins.
        """
        candidates = self.headwords.get(fold_accents(word), [])
        exact = [lex for lex in candidates if lex.lemma == word]
        if exact:
            return exact[0]
        return candidates[0] if len({lex.lemma for lex in candidates}) == 1 else None

    def find(self, analysis: Analysis) -> Lexeme | None:
        """Steps 1-2 of resolve: a lexeme already in `lexemes`. Never writes."""
        lemma, pos = analysis
        if (lexeme_id := self.ids.get(analysis)) is not None:
            return Lexeme(lexeme_id, lemma, pos)
        accented = self.by_folded.get((fold_accents(lemma), pos), [])
        if len(accented) == 1:
            return Lexeme(self.ids[accented[0], pos], accented[0], pos)
        return None

    def in_dictionary(self, analysis: Analysis) -> bool:
        """Whether resolve's step 3 would add this word. Never writes."""
        return dictionary_definition(self.wiktionary, self.expressions, analysis) is not False

    def resolve(self, analysis: Analysis) -> Lexeme | None:
        if (found := self.find(analysis)) is not None:
            return found
        lemma, pos = analysis
        lexeme_id = ensure_lexeme(self.conn, self.wiktionary, lemma, pos, self.expressions)
        if lexeme_id is None:
            return None
        self.add(lexeme_id, lemma, pos)
        return Lexeme(lexeme_id, lemma, pos)

    def lookup(self, analysis: Analysis) -> Lexeme | None:
        """An exact match only: never guesses accents, never writes."""
        lexeme_id = self.ids.get(analysis)
        return None if lexeme_id is None else Lexeme(lexeme_id, *analysis)


@dataclass(frozen=True)
class Event:
    """One row for the word_events log. The mode follows from the event type."""

    lexeme_id: int
    event_type: str  # taught | seen | looked_up | used
    source: str  # seed | pre_teach | conversation | lyrics | reading
    grade: int | None = None
    turn_id: int | None = None

    @property
    def mode(self) -> str:
        return "production" if self.event_type == "used" else "recognition"


def log_events(conn: sqlite3.Connection, events: Iterable[Event]) -> None:
    """Append events to the log; the trigger keeps word_bank current."""
    conn.executemany(
        """
        INSERT INTO word_events (lexeme_id, mode, event_type, source, grade, turn_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [(e.lexeme_id, e.mode, e.event_type, e.source, e.grade, e.turn_id) for e in events],
    )
