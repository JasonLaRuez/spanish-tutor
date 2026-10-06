"""The lyrics skill (roadmap Phase 4, slice 4.3; Jason's design, 2026-10-05).

A lyrics session is a reading session (reading.py) of a song or poem, with skill and
event source 'lyrics': its new words are studied the same way, it can be read with
lookups, finished, and talked about. What it adds is translation:

1. **Try first.** The learner translates some lines into English (by default the first
   stanza). That's comprehension, not production, so nothing is graded or credited.
2. **Compare.** Every line shows side by side: the Spanish, the learner's attempt, a
   **natural** translation (the meaning, as an English speaker would say it), a **literal**
   one (word for word, idioms kept literal) and a note where the two differ; plus a short
   comment on each attempt.

The translation is grounded (the retrieval step the evaluation slice measures): the
song's expressions are found when its text is analyzed (lexicon.ExpressionMatcher), and
the prompt gives each one's reviewed meaning and the lexicon's real Tatoeba example, which
the natural translation must use. It's one structured call per song, at medium effort,
stored as a run in song_translations (append-only; the latest run is shown) and reused by
every later session. The comparison is one call per submitted attempt, logged as a pair of
`attempt` turns with its cost.
"""

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from spanish_tutor.conversation import Generation
from spanish_tutor.reading import ReadingSession

Ask = Callable[[type[BaseModel], str], Generation]  # ClaudeGenerator.ask


class LineTranslation(BaseModel):
    line_no: int = Field(description="The line's number, as listed.")
    natural_en: str = Field(
        description="A natural English translation of the line: what it means, as an English "
        "speaker would say it. Expressions and figures of speech by their meaning."
    )
    literal_en: str = Field(
        description="A literal translation: word for word, keeping the Spanish order where "
        "English allows, with every expression and idiom translated word by word."
    )
    # Required but nullable, like TutorReply.note_en: the schema shape the API accepts.
    note_en: str | None = Field(
        description="Only where the natural and literal translations differ in meaning (an "
        "idiom, a figure of speech, a cultural reference): one short sentence explaining the "
        "figurative meaning. Null otherwise."
    )


class SongTranslation(BaseModel):
    lines: list[LineTranslation]


class LineFeedback(BaseModel):
    line_no: int
    verdict: Literal["right", "close", "missed"] = Field(
        description="right: the attempt has the line's meaning (wording may differ); close: "
        "mostly, with something missed or too literal; missed: the meaning is wrong."
    )
    comment_en: str = Field(
        description="One or two short, encouraging sentences: what the attempt got right, "
        "and what it missed (often the figurative meaning)."
    )


class AttemptFeedback(BaseModel):
    lines: list[LineFeedback]


@dataclass(frozen=True)
class Expression:
    """An expression in the song, with what grounds its translation."""

    line_no: int
    phrase: str
    definition_en: str | None  # the reviewed sense (ingest/expressions.py)
    example_es: str | None  # the lexicon's Tatoeba example, with its human translation
    example_en: str | None


@dataclass(frozen=True)
class Comparison:
    line_no: int
    es: str
    attempt: str | None
    natural_en: str
    literal_en: str
    note_en: str | None
    verdict: str | None
    comment_en: str | None


class TranslationError(Exception):
    """The model's translation didn't cover every line exactly once, even after a retry."""


TRANSLATION_INSTRUCTIONS = """\
Translate this {kind} for a Spanish learner, line by line, twice:

- natural_en: what the line means, as an English speaker would naturally say it. Render
  expressions and figures of speech by their meaning, not word by word.
- literal_en: word for word, keeping the Spanish order where English allows and translating
  every expression literally, so the learner can see how the Spanish is built.
- note_en: only where the two differ in meaning (an idiom, a figure of speech, a cultural
  reference), one short sentence on the figurative meaning; otherwise null.

Translate each line on its own (a line may be part of a sentence that continues), and
answer every line number exactly once.
"""


def translation_prompt(
    kind: str, title: str, lines: list[str], expressions: list[Expression]
) -> str:
    prompt = TRANSLATION_INSTRUCTIONS.format(kind=kind) + f"\n«{title}»\n"
    prompt += "\n".join(f"{n}. {line}" for n, line in enumerate(lines, 1))
    if expressions:
        prompt += (
            "\n\nThese lines contain fixed expressions. Use the meaning given here for the "
            "natural translation (and translate them word by word in the literal one):\n"
        )
        for e in expressions:
            prompt += f"- line {e.line_no}: «{e.phrase}» means: {e.definition_en or '?'}"
            if e.example_es:
                prompt += f' (as in: «{e.example_es}» = "{e.example_en}")'
            prompt += "\n"
    return prompt


def feedback_prompt(kind: str, rows: list[tuple[int, str, str, LineTranslation]]) -> str:
    listing = "\n\n".join(
        f"{n}. Spanish: {es}\n   Learner: {attempt}\n   Natural: {t.natural_en}\n"
        f"   Literal: {t.literal_en}" + (f"\n   Note: {t.note_en}" if t.note_en else "")
        for n, es, attempt, t in rows
    )
    return (
        f"A Spanish learner translated these lines of a {kind} into English before seeing any "
        "translation. For each, judge whether the attempt has the line's meaning, and comment "
        "briefly and encouragingly, in English. Judge meaning, not wording: a free translation "
        "that keeps the meaning is right. Answer every number exactly once.\n\n" + listing
    )


class LyricsSession(ReadingSession):
    """A reading session of a song or poem, with translation and attempts.

    `translate` asks for the translation (ClaudeGenerator(effort="medium").ask in the app);
    the comparison uses the session's own generator (`generate.ask`, low effort).
    """

    skill = "lyrics"

    def __init__(self, *args, translate: Ask, **kwargs):
        super().__init__(*args, **kwargs)
        self.translate = translate
        self._translation: list[LineTranslation] | None = None

    # --- Grounding ---

    def expressions(self) -> list[Expression]:
        """The expressions in the song, by line, with their reviewed sense and an example."""
        found = []
        for line_no, tokens in enumerate(self.tokens, 1):
            for _form, lex in tokens:
                if lex is None or lex.pos != "EXPR":
                    continue
                row = self.conn.execute(
                    "SELECT definition_en, example_es, example_en FROM lexemes WHERE lexeme_id = ?",
                    (lex.lexeme_id,),
                ).fetchone()
                found.append(Expression(line_no, lex.lemma, row[0], row[1], row[2]))
        return found

    # --- The translation (stored once per song) ---

    def translation(self) -> list[LineTranslation]:
        """The song's translation: the latest stored run, else a new one (one model call)."""
        if self._translation is None:
            self._translation = stored_translation(self.conn, self.content_id, len(self.units))
        if self._translation is None:
            self._translation = self._new_translation()
        return self._translation

    def _new_translation(self) -> list[LineTranslation]:
        prompt = translation_prompt(
            self.item["kind"], self.item["title"], self.units, self.expressions()
        )
        started = time.perf_counter()
        generations = []
        for attempt in range(2):
            generation = self.translate(
                SongTranslation,
                prompt
                if attempt == 0
                else prompt + "\n\nYour previous answer missed or repeated line numbers: give "
                f"exactly one translation for each of lines 1 to {len(self.units)}.",
            )
            generations.append(generation)
            lines = generation.reply.lines
            if sorted(line.line_no for line in lines) == list(range(1, len(self.units) + 1)):
                break
        else:
            raise TranslationError(
                f"the translation of «{self.item['title']}» didn't cover its "
                f"{len(self.units)} lines exactly once"
            )
        lines = sorted(lines, key=lambda line: line.line_no)
        with self.conn:
            run = self.conn.execute(
                """
                INSERT INTO song_translations
                    (content_id, model, input_tokens, cache_read_tokens, output_tokens, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    self.content_id,
                    self.model,
                    sum(g.input_tokens for g in generations),
                    sum(g.cache_read_tokens for g in generations),
                    sum(g.output_tokens for g in generations),
                    round((time.perf_counter() - started) * 1000),
                ),
            ).lastrowid
            self.conn.executemany(
                "INSERT INTO song_translation_lines VALUES (?, ?, ?, ?, ?)",
                [
                    (run, line.line_no, line.natural_en.strip(), line.literal_en.strip(),
                     (line.note_en or "").strip() or None)
                    for line in lines
                ],
            )  # fmt: skip
        return lines

    # --- Try first, then compare ---

    def attempt(self, attempts: dict[int, str]) -> list[Comparison]:
        """Compare the learner's own translations of some lines with the song's translation.

        Lines not attempted come back without a verdict. One call for all the attempts;
        logged as a learner `attempt` turn (the Spanish lines, the learner's English in
        note_en) and a tutor `attempt` turn (the comments, and the call's cost).
        """
        translation = self.translation()
        tried = {n: text.strip() for n, text in attempts.items() if text.strip()}
        tried = {n: text for n, text in tried.items() if 1 <= n <= len(self.units)}
        feedback: dict[int, LineFeedback] = {}
        if tried:
            rows = [
                (n, self.units[n - 1], text, translation[n - 1])
                for n, text in sorted(tried.items())
            ]
            started = time.perf_counter()
            generation = self.generate.ask(
                AttemptFeedback, feedback_prompt(self.item["kind"], rows)
            )
            for line in generation.reply.lines:
                if line.line_no in tried:
                    feedback.setdefault(line.line_no, line)
            spanish = "\n".join(self.units[n - 1] for n in sorted(tried))
            with self.conn:
                self._add_turn(
                    "learner", "attempt", spanish,
                    note_en="\n".join(f"{n}: {text}" for n, text in sorted(tried.items())),
                )  # fmt: skip
                self._add_turn(
                    "tutor",
                    "attempt",
                    spanish,
                    note_en="\n".join(
                        f"{n} ({f.verdict}): {f.comment_en}" for n, f in sorted(feedback.items())
                    ),
                    input_tokens=generation.input_tokens,
                    cache_read_tokens=generation.cache_read_tokens,
                    output_tokens=generation.output_tokens,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                )
        return [
            Comparison(
                line_no=n,
                es=self.units[n - 1],
                attempt=tried.get(n),
                natural_en=t.natural_en,
                literal_en=t.literal_en,
                note_en=t.note_en,
                verdict=feedback[n].verdict if n in feedback else None,
                comment_en=feedback[n].comment_en if n in feedback else None,
            )
            for n, t in enumerate(translation, 1)
        ]


def stored_translation(
    conn: sqlite3.Connection, content_id: int, lines: int
) -> list[LineTranslation] | None:
    """The latest stored translation of an item, if it covers exactly `lines` lines (a text
    whose lines changed since is translated again)."""
    run = conn.execute(
        "SELECT MAX(translation_id) FROM song_translations WHERE content_id = ?", (content_id,)
    ).fetchone()[0]
    if run is None:
        return None
    rows = conn.execute(
        """
        SELECT line_no, natural_en, literal_en, note_en FROM song_translation_lines
        WHERE translation_id = ? ORDER BY line_no
        """,
        (run,),
    ).fetchall()
    if [r[0] for r in rows] != list(range(1, lines + 1)):
        return None
    return [
        LineTranslation(line_no=r[0], natural_en=r[1], literal_en=r[2], note_en=r[3]) for r in rows
    ]
