"""English inside songs: which words on each line are English, and which are English
loanwords used as Spanish, marked once per song.

Many modern songs (Mexican, Puerto Rican) mix Spanish and English, and words are resolved to
the lexicon one at a time, without context: an English "come" (come with me) would count as
Spanish comer, "me" as the pronoun me, "dance" as danzar. So when a song is indexed, one
structured model call reads its lines and names, judged in context:

- English words (kind 'english'): the song switching into English. Every step that reads
  the text skips them on that line: indexing, the study list, finishing, lookups, the lines
  offered for Try first, and the translation, which keeps them as written.
- English loanwords (kind 'loanword'): words of English origin that Spanish speakers use as
  Spanish (baby, party, show, okay, email, and adapted ones such as fútbol, líder) inside a
  Spanish phrase. They are Spanish vocabulary and are counted and taught like any word; they
  are only marked, so the reader sees which words an English speaker already knows.

The marks are stored in english_checks and english_words (migration 11). Only songs are
checked; poems, stories and chapters never are.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from spanish_tutor.conversation import Generation

Ask = Callable[[type[BaseModel], str], "Generation"]  # ClaudeGenerator.ask
KINDS = ("english", "loanword")
Marks = dict[int, dict[str, str]]  # line number (1-based) -> lowercase word -> kind

# Opus 5.5 list prices ($ per million tokens) and sizes for the estimate shown before calls.
# Measured: three real songs (211 lines) took 5,952 in / 293 out tokens (3¢), so ~17 input
# tokens a line and little output, since most lines have no English; the dense made-up
# lines of tests/test_english_live.py (5 lines, 885 in / 157 out) set the output base.
PRICE_IN, PRICE_OUT = 4.0, 20.0
EST_PROMPT_TOKENS, EST_TOKENS_PER_LINE_IN = 800, 17
EST_OUTPUT_TOKENS, EST_TOKENS_PER_LINE_OUT = 60, 3

# English contractions split by the tagger: "don't" -> do + n't. Each part of a marked word
# is skipped too.
APOSTROPHES = re.compile(r"[’']")


class EnglishLine(BaseModel):
    line_no: int = Field(description="The line's number, as listed.")
    english: list[str] = Field(
        description="The line's English words (the song switching into English), each "
        "exactly as written in the line."
    )
    loanwords: list[str] = Field(
        description="English loanwords used as Spanish in this line, each exactly as written."
    )


class EnglishWords(BaseModel):
    """The lines that contain English or English loanwords."""

    lines: list[EnglishLine] = Field(
        description="Only lines with at least one English word or loanword."
    )


INSTRUCTIONS = """\
These are the lines of a song in Spanish that may mix in English. For each line, list:

- english: words where the song switches into English. Judge each word in its line: "me" in
  "come with me" is English, "me" in "me gusta" is Spanish.
- loanwords: words of English origin that Spanish speakers commonly use as Spanish words,
  inside a Spanish phrase: "la party", "mi baby", "un show", "okay", "el email", and adapted
  spellings such as "fútbol" or "líder". A loanword inside an English phrase is english.

Names of people, places and brands are neither: leave them out. Skip lines with neither.
"""


def prompt(title: str, lines: list[str]) -> str:
    numbered = "\n".join(f"{n}. {line}" for n, line in enumerate(lines, 1))
    return f"{INSTRUCTIONS}\n«{title}»\n{numbered}"


def words_of(line: str) -> set[str]:
    """A line's words, lowercase (apostrophes kept: don't, I'm)."""
    return {w.lower() for w in re.findall(r"[\w’']+", line)}


def mark(title: str, lines: list[str], ask: Ask) -> tuple[Marks, Generation]:
    """One call: each line's English words and loanwords. A word the line doesn't contain,
    or a line number that doesn't exist, is dropped rather than trusted; a word given as both
    counts as English."""
    generation = ask(EnglishWords, prompt(title, lines))
    marks: Marks = {}
    for entry in generation.reply.lines:
        if not 1 <= entry.line_no <= len(lines):
            continue
        present = words_of(lines[entry.line_no - 1])
        line = marks.setdefault(entry.line_no, {})
        for kind, words in (("loanword", entry.loanwords), ("english", entry.english)):
            for word in {w.lower().strip() for w in words} & present:
                line[word] = kind  # english, written second, wins a tie
        if not line:
            del marks[entry.line_no]
    return marks, generation


def estimate_cost(lines: int) -> float:
    tokens_in = EST_PROMPT_TOKENS + lines * EST_TOKENS_PER_LINE_IN
    tokens_out = EST_OUTPUT_TOKENS + lines * EST_TOKENS_PER_LINE_OUT
    return (tokens_in * PRICE_IN + tokens_out * PRICE_OUT) / 1e6


# --- Stored marks ---------------------------------------------------------------------------


def checked(conn: sqlite3.Connection, content_id: int) -> bool:
    found = conn.execute("SELECT 1 FROM english_checks WHERE content_id = ?", (content_id,))
    return found.fetchone() is not None


def store(
    conn: sqlite3.Connection,
    content_id: int,
    marks: Mapping[int, Mapping[str, str]],
    model: str,
    generation: Generation | None = None,
) -> None:
    """Record a song's check (replacing an earlier one) and its marked words. Writes in
    the caller's transaction."""
    conn.execute("DELETE FROM english_words WHERE content_id = ?", (content_id,))
    conn.execute("DELETE FROM english_checks WHERE content_id = ?", (content_id,))
    conn.execute(
        "INSERT INTO english_checks (content_id, model, input_tokens, output_tokens) "
        "VALUES (?, ?, ?, ?)",
        (
            content_id,
            model,
            generation and generation.input_tokens + generation.cache_read_tokens,
            generation and generation.output_tokens,
        ),
    )
    conn.executemany(
        "INSERT INTO english_words (content_id, line_no, word, kind) VALUES (?, ?, ?, ?)",
        [(content_id, n, w, k) for n, words in marks.items() for w, k in sorted(words.items())],
    )


def load_marks(conn: sqlite3.Connection, content_id: int) -> Marks:
    """A song's marked words per line, with their kind (empty if none, or never checked)."""
    marks: Marks = {}
    for line_no, word, kind in conn.execute(
        "SELECT line_no, word, kind FROM english_words WHERE content_id = ?", (content_id,)
    ):
        marks.setdefault(line_no, {})[word] = kind
    return marks


def load(conn: sqlite3.Connection, content_id: int) -> dict[int, set[str]]:
    """A song's English words per line: the ones every step skips (not the loanwords)."""
    return {
        line_no: english
        for line_no, words in load_marks(conn, content_id).items()
        if (english := {w for w, kind in words.items() if kind == "english"})
    }


def is_english(form: str, words: set[str] | None) -> bool:
    """Whether a token on a line is one of its English words. A token is a word as the
    tagger split it: "don't" arrives as "do" and "n't", and an expression as several words,
    so a token counts when each of its words is English or part of an English word."""
    if not words:
        return False
    parts = set(words)
    for word in words:
        pieces = APOSTROPHES.split(word)
        parts.update(pieces)
        parts.update("'" + p for p in pieces[1:])  # 'm, 's, 're
        parts.update(p[:-1] for p in pieces if p.endswith("n"))  # do(n) + n't
        if re.search(r"n['’]t$", word):
            parts.add("n't")
    return all(w in parts for w in form.lower().split())
