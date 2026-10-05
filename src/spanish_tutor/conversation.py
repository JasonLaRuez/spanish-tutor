"""The conversation skill: chat in Spanish inside the learner's vocabulary.

    uv run python -m spanish_tutor.conversation [--topic "el jardín"]

Each turn:
1. The learner's message is analyzed. Every real word in it is logged as `used`
   (production); words Claude flags as misused get a lower grade, and a wrong word (not
   the one the learner meant: "jugo" for "juego") never enters production. A word the learner
   uses before it was ever taught also enters the recognition bank.
2. Claude replies, constrained to the recognition vocabulary. Tatoeba sentences the
   learner can read, retrieved by similarity, are added as level and topic anchors (RAG).
3. The reply is checked against the word bank. One new word is allowed (i+1); two or
   more trigger one retry naming them. Whatever new words remain are taught, with a
   definition and a readable example; every other word in the reply is logged as `seen`.

Everything is logged in one transaction per turn: the transcript and its metrics in
`turns`, and the word events, each linked to the turn that caused it.

Prompt layout, for caching: the instructions and the vocabulary list form a system
prompt that stays byte-identical for the whole session (the vocabulary is frozen when
the session starts), cached for 1 hour. Per-turn context (examples, words taught this
session) goes in a system note after each learner message, so it never invalidates the
cached prefix. The conversation itself is cached too, with a second, 5-minute breakpoint
on the latest learner message: each request reads everything up to the previous one and
writes only what the last exchange added (measured on sessions 1-2, 2026-10-05: 23-28%
cheaper per session than re-sending the history at full price).
"""

import argparse
import re
import sqlite3
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from langchain_chroma import Chroma
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from spanish_tutor import db, progress
from spanish_tutor.config import MODEL
from spanish_tutor.keyboard import expand_markers
from spanish_tutor.lexicon import Analysis, TokenAnalysis, normalize_text
from spanish_tutor.teaching import Lesson, lesson
from spanish_tutor.topics import (
    MAX_WORDS,
    MIN_WORDS,
    TopicWords,
    candidate_count,
    choose_words,
    topic_candidates,
    word_count,
)
from spanish_tutor.vectorstore import search_sentences
from spanish_tutor.words import (
    GRADE_MISUSED,
    GRADE_USED,
    Event,
    Lexeme,
    LexiconIndex,
    log_events,
)

SOURCE = "conversation"
INSTRUCTIONS = (Path(__file__).parent / "prompts" / "conversation.md").read_text(encoding="utf-8")
MAX_NEW_WORDS = 1  # per reply (i+1); more triggers one retry
EXAMPLES_PER_TURN = 4
PUNCTUATION = ".,;:!?¡¿\"'«»()"

POS_NAMES = {
    "ADJ": "adjectives",
    "ADP": "prepositions",
    "ADV": "adverbs",
    "CCONJ": "conjunctions",
    "DET": "determiners",
    "EXPR": "expressions",
    "INTJ": "interjections",
    "NOUN": "nouns",
    "NUM": "numbers",
    "PART": "particles",
    "PRON": "pronouns",
    "SCONJ": "subordinating conjunctions",
    "VERB": "verbs",
}


class Misuse(BaseModel):
    """A word the learner used wrongly, and whether it was the wrong word or the wrong form."""

    written: str = Field(description="The word or short phrase exactly as the learner wrote it.")
    wrong_word: bool = Field(
        description="True when it is a different word from the one the learner meant: the "
        "word for something else (preguntar for pedir, ser for estar), a misspelling that "
        "made another real word (jugo for juego), or an English word. False when it is the "
        "word they meant in the wrong form: a wrong conjugation, tense, gender or number, or "
        "a misspelling that is still clearly that word."
    )


class TutorReply(BaseModel):
    """The structured output of every conversation request: replies and translations.

    One schema on purpose. The output schema is part of the cached prompt, so a second
    schema gets a cache entry of its own (measured 2026-10-02: the same ~5k-token prefix
    was written once per schema). Translation turns therefore use this schema too, with
    the Spanish in reply_es and the explanation in note_en, and they read the cache the
    conversation already wrote.
    """

    reply_es: str = Field(
        description="Your reply to the learner, in Spanish only. For a translation request: "
        "only the Spanish the learner asked for."
    )
    reply_en: str = Field(description="A natural English translation of reply_es.")
    misused: list[Misuse] = Field(
        description="Words from the learner's last message that were used wrongly. Empty "
        "if none, and for a translation request."
    )
    note_en: str | None = Field(
        description="A short English note: the most important mistake with the corrected "
        "Spanish, or for a translation request, the word choices or grammar to notice. "
        "Null if there is nothing to say."
    )


class SessionNotes(BaseModel):
    """The tutor's notes when a conversation ends (one request per session, own schema)."""

    went_well_en: str = Field(
        description="One or two sentences in English, addressed to the learner, on what "
        "they did well in this conversation. Be specific to this conversation."
    )
    work_on: list[str] = Field(
        description="Two or three short, concrete things to practice next, in English, "
        "addressed to the learner. Base them on this conversation's corrections and "
        "mistakes (quote the Spanish), or on today's words the learner didn't use."
    )


@dataclass(frozen=True)
class Generation:
    reply: Any  # the structured output: TutorReply, TopicWords or SessionNotes
    input_tokens: int = 0  # not read from the cache (includes cache writes)
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0  # part of input_tokens, written at 1.25x
    cache_write_1h_tokens: int = 0  # part of input_tokens, written at 2x
    output_tokens: int = 0  # includes thinking


class ReplyGenerator(Protocol):
    def __call__(self, messages: list[BaseMessage]) -> Generation:
        """A conversation reply or a translation (TutorReply)."""

    def select_words(self, prompt: str) -> TopicWords:
        """Topic words chosen from a candidate list (see topics.choose_words)."""

    def summarize(self, prompt: str) -> Generation:
        """The tutor's notes on a finished conversation (SessionNotes)."""


class ClaudeGenerator:
    """Claude via LangChain, returning structured output (TutorReply, TopicWords).

    Opus 5.5 rejects sampling parameters and forced tool calls, so: no temperature, and
    structured output via `json_schema` (output_config.format) rather than LangChain's
    default forced-tool method. `effort="low"` suits short chat turns.

    Server-side refusal fallback is on: if a safety classifier declines a request (a
    false positive is the only plausible case for a language tutor), the API reruns it
    on a fallback model instead of returning nothing.
    """

    def __init__(self, model: str = MODEL, effort: str = "low", max_tokens: int = 4000):
        from langchain_anthropic import ChatAnthropic

        self.llm = ChatAnthropic(
            model=model,
            effort=effort,
            max_tokens=max_tokens,
            betas=["server-side-fallback-2026-07-01"],
            model_kwargs={"fallbacks": "default"},
        )
        self.chains: dict[type[BaseModel], Any] = {}

    def __call__(self, messages: list[BaseMessage]) -> Generation:
        return self._invoke(TutorReply, messages)

    def select_words(self, prompt: str) -> TopicWords:
        return self._invoke(TopicWords, [HumanMessage(prompt)]).reply

    def summarize(self, prompt: str) -> Generation:
        return self._invoke(SessionNotes, [HumanMessage(prompt)])

    def _invoke(self, schema: type[BaseModel], messages: list[BaseMessage]) -> Generation:
        if schema not in self.chains:
            self.chains[schema] = self.llm.with_structured_output(
                schema, method="json_schema", include_raw=True
            )
        result = self.chains[schema].invoke(messages)
        if result["parsing_error"] is not None:
            raise result["parsing_error"]
        usage = result["raw"].usage_metadata or {}
        details = usage.get("input_token_details") or {}
        cache_read = details.get("cache_read", 0) or 0
        return Generation(
            reply=result["parsed"],
            input_tokens=usage.get("input_tokens", 0) - cache_read,
            cache_read_tokens=cache_read,
            cache_write_5m_tokens=details.get("ephemeral_5m_input_tokens", 0) or 0,
            cache_write_1h_tokens=details.get("ephemeral_1h_input_tokens", 0) or 0,
            output_tokens=usage.get("output_tokens", 0),
        )


def clean_note(note: str | None) -> str | None:
    """Claude's English note without stray characters it occasionally leaves at the end.

    Seen in session 1: `...detrás de mi casa."}` and `...'le gusta mirarlo'.'`. Only an
    unbalanced trailing brace or quote is removed; a note that legitimately ends in a
    quotation keeps it.
    """
    if note is None:
        return None
    note = note.strip()
    while note:
        last = note[-1]
        stray_brace = last == "}" and note.count("}") > note.count("{")
        stray_quote = last in "\"'" and note.count(last) % 2 == 1
        if not (stray_brace or stray_quote):
            break
        note = note[:-1].rstrip()
    return note or None


# --- Prompt ----------------------------------------------------------------------------


def vocabulary_block(known: set[Analysis]) -> str:
    """The learner's vocabulary, grouped by POS and sorted: byte-stable, so cacheable."""
    by_pos: dict[str, list[str]] = defaultdict(list)
    for lemma, pos in known:
        by_pos[pos].append(lemma)
    lines = [
        f"{POS_NAMES.get(pos, pos)}: {', '.join(sorted(by_pos[pos]))}" for pos in sorted(by_pos)
    ]
    return "# The learner's vocabulary\n\n" + "\n".join(lines)


def system_message(known: set[Analysis]) -> SystemMessage:
    return SystemMessage(
        content=[
            {"type": "text", "text": INSTRUCTIONS},
            # The breakpoint caches instructions + vocabulary: the whole stable prefix. One
            # hour, not the default five minutes: learners pause to think (session 1 had a
            # 33-minute gap, after which ~5k tokens were re-sent at full price).
            {
                "type": "text",
                "text": vocabulary_block(known),
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
        ]
    )


def turn_note(
    examples: list[str],
    taught: list[str],
    avoid: list[str] | None = None,
    focus: list[str] | None = None,
) -> str:
    parts = []
    if examples:
        parts.append("Sentences the learner can read:\n" + "\n".join(f"- {s}" for s in examples))
    if focus:
        parts.append(focus_line(focus))
    parts.append(
        "Words taught this session: " + (", ".join(taught) if taught else "none yet") + "."
    )
    if avoid:
        parts.append(
            "Your previous draft used these words outside the learner's vocabulary: "
            + ", ".join(avoid)
            + f". Rewrite the reply without them, using at most {MAX_NEW_WORDS} unlisted word."
        )
    return "\n\n".join(parts)


# "¿Cómo se dice ...?": tolerant of missing accents, typed markers (c'omo), a missing ¿,
# quotes or none, and an optional "en español".
_HOW_TO_SAY = re.compile(
    r"^\s*[¿?]?\s*c(?:o|ó|'o)mo\s+se\s+dice\s+(?P<phrase>.+?)"
    r"\s*(?:en\s+espa(?:ñ|~n|n)ol)?\s*[?.!]*\s*$",
    re.IGNORECASE | re.DOTALL,
)
_QUOTES = {'"': '"', "“": "”", "«": "»", "'": "'", "‘": "’"}


def parse_translation_request(text: str) -> str | None:
    """The phrase in "¿Cómo se dice "<phrase>" en español?", or None for anything else."""
    if (match := _HOW_TO_SAY.match(text)) is None:
        return None
    phrase = match.group("phrase").strip()
    if len(phrase) > 1 and phrase[0] in _QUOTES and phrase.endswith(_QUOTES[phrase[0]]):
        phrase = phrase[1:-1].strip()
    return phrase or None


def translation_note(phrase: str) -> str:
    return (
        f'The learner paused the conversation to ask how to say this in Spanish: "{phrase}". '
        "Give the most natural Spanish a native speaker would use in this conversation. "
        "Use words from the learner's vocabulary where they sound natural, but never at the "
        "cost of naturalness: the app teaches any new words. Answer only this; don't "
        "continue the conversation. In your reply, reply_es is only that Spanish, misused "
        "is empty, and note_en explains in one to three short sentences the word choices "
        "or grammar the learner should notice."
    )


# A goodbye at the end of a message ends the conversation: "¡Hasta luego!", "Bueno, tengo
# que irme. ¡Adiós!", "Gracias, nos vemos", "¡Chao, profesor!". Checked after accent
# markers are expanded, and lenient about missing accents ("adios", "hasta manana").
_FAREWELL = re.compile(
    r"(?:^|[\s¡¿,.;:!?\-—])"
    r"(?:hasta\s+(?:luego|ma[ñn]ana|pronto|la\s+pr[oó]xima(?:\s+vez)?)|adi[oó]s|cha[ou]"
    r"|nos\s+vemos(?:\s+(?:pronto|luego|ma[ñn]ana))?)"
    r"(?:\s*,\s*[^\W\d_]+(?:\s+[^\W\d_]+)?)?"  # an optional "..., profesor"
    r"[\s!.…)\]]*$",
    re.IGNORECASE,
)


def is_farewell(text: str) -> bool:
    """Whether a learner message ends with a goodbye, which ends the conversation."""
    return _FAREWELL.search(text.strip()) is not None


FAREWELL_NOTE = (
    "The learner is ending the conversation now. Reply with a short, warm goodbye in "
    "Spanish (one or two sentences) and don't ask a question. If their message has a "
    "mistake, still give the note."
)
# When the learner ends the conversation without writing a goodbye (the app's button).
GOODBYE_MESSAGE = (
    "[The learner has ended the conversation. Say a short, warm goodbye in Spanish (one or "
    "two sentences) and don't ask a question.]"
)


def summary_prompt(topic: str | None, transcript: list[dict], stats: dict) -> str:
    """The request for the tutor's notes: the transcript, its corrections, and the numbers."""
    lines = []
    for turn in transcript:
        if turn["role"] == "learner":
            label = "Learner (¿cómo se dice?)" if turn["kind"] == "translation" else "Learner"
        else:
            label = "Tutor"
        lines.append(f"{label}: {turn['text_es']}")
        if turn["role"] == "tutor" and turn["note_en"]:
            lines.append(f"  (note to the learner: {clean_note(turn['note_en'])})")
    unused = [w for w in stats["pre_taught"] if w not in stats["pre_taught_used"]]
    numbers = [
        f"- Messages from the learner: {stats['messages']}",
        f"- Corrections given: {stats['corrections']}",
        f'- "¿Cómo se dice?" questions: {stats["how_to_say"]}',
        (
            "- Words the learner used for the first time ever: "
            f"{', '.join(stats['first_time']) or 'none'}"
        ),
    ]
    if stats["pre_taught"]:
        numbers.append(
            f"- Today's topic words, taught before the conversation: "
            f"{', '.join(stats['pre_taught'])}. Not used by the learner: "
            f"{', '.join(unused) or 'none'}."
        )
    about = f" about {topic}" if topic else ""
    return (
        f"You are a Spanish tutor. Your learner (intermediate, about A2) has just finished a "
        f"conversation{about} with you. Below are the transcript, with the correction notes "
        "you gave, and the session's numbers. Write the learner a short summary: what went "
        "well, and two or three concrete things to practice next. Base it only on what is "
        "below; don't invent mistakes. Be encouraging and specific.\n\n"
        "# Transcript\n" + "\n".join(lines) + "\n\n# Numbers\n" + "\n".join(numbers)
    )


def learner_message(text: str, cached: bool = False) -> HumanMessage:
    """A learner (or app) message as one text block; `cached` marks the conversation's
    cache breakpoint. The history keeps the same block without the mark, so the bytes the
    next request sends up to here are identical and the cache entry is read back."""
    block: dict = {"type": "text", "text": text}
    if cached:
        block["cache_control"] = {"type": "ephemeral"}  # 5 minutes, after the 1-hour system
    return HumanMessage(content=[block])


def focus_line(focus: list[str]) -> str:
    # The words are taught so the learner has the topic's vocabulary, and the learner is the
    # one meant to practice them (Jason, 2026-10-05): the tutor uses some, not all.
    return (
        "Words the learner was taught for today's topic, before the conversation: "
        + ", ".join(focus)
        + ". They are for the learner to practice: ask questions that invite the learner to "
        "use them. Use some yourself where they fit naturally; you don't need to use them all."
    )


def opening_message(topic: str | None, focus: list[str] | None = None) -> str:
    if not topic:
        return "[The learner has started a conversation. Greet them and ask what they'd like to talk about.]"
    opening = f"The learner has started a conversation about: {topic}. Greet them and begin."
    if focus:
        opening += " " + focus_line(focus)
    return f"[{opening}]"


# --- The skill -------------------------------------------------------------------------


@dataclass
class TutorTurn:
    """What the learner sees after a turn."""

    reply_es: str
    reply_en: str
    note_en: str | None
    lessons: list[Lesson]
    retried: bool
    draft_out_of_bank: int
    not_words: list[str] = field(default_factory=list)  # learner tokens that aren't words
    used: list[str] = field(default_factory=list)  # words (lemmas) the learner used this turn


@dataclass
class TranslationTurn:
    """The answer to "¿cómo se dice ...?", which pauses the conversation for a turn."""

    spanish: str
    explanation_en: str | None
    lessons: list[Lesson]
    pending: str | None  # the tutor's last message, which the learner still has to answer


@dataclass
class Ending:
    """The end of a conversation: the tutor's goodbye, the stats, and the tutor's notes."""

    turn: TutorTurn  # the goodbye
    stats: dict  # progress.session_stats
    went_well_en: str | None  # None if the notes couldn't be written
    work_on: list[str]
    notes_error: str | None = None


class Tutor:
    """One conversation session. Reads and writes the word bank on every turn."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        generate: ReplyGenerator,
        index: LexiconIndex,
        analyze: Callable[[str], list[TokenAnalysis]],
        store: Chroma | None = None,
        topic: str | None = None,
        model: str = MODEL,
    ):
        self.conn = conn
        self.generate = generate
        self.index = index
        self.analyze = analyze
        self.store = store
        self.topic = topic
        self.model = model
        # Live recognition set: grows as words are taught or used during the session.
        self.known = db.known_vocabulary(conn)
        # Frozen when the conversation opens (after any pre-teaching), so the cached
        # prompt prefix never changes during the session.
        self._frozen_system: SystemMessage | None = None
        self.history: list[BaseMessage] = []
        self.taught: list[Lexeme] = []
        self.focus: list[Lexeme] = []  # pre-taught topic words
        self._pre_taught_unlogged: list[Lexeme] = []
        # Why fewer topic words were taught than requested (for the learner), if they were.
        self.pre_teach_shortfall: str | None = None
        self.turn_no = 0
        self.last_tutor_turn_id: int | None = None
        self.last: TutorTurn | None = None
        self.ended = False
        with conn:
            self.session_id = db.start_session(conn, "conversation", model, topic)

    @property
    def system(self) -> SystemMessage:
        if self._frozen_system is None:
            self._frozen_system = system_message(self.known)
        return self._frozen_system

    # --- Turns ---

    def pre_teach(self, n: int) -> list[Lesson]:
        """Teach n topic words before the conversation opens (call before `open`).

        Candidates come from the Tatoeba sentences nearest the topic, and Claude picks the
        most useful (topics.py). It may pick fewer than n when too few are really about the
        topic; `pre_teach_shortfall` then says why. The words join the vocabulary the
        prompt is frozen with, and every turn's note asks the tutor to invite the learner
        to use them. Their `taught` events (source 'pre_teach') are logged with the
        opening turn.
        """
        if not self.topic or self.store is None or self._frozen_system is not None:
            return []
        candidates = topic_candidates(
            self.conn, self.store, self.topic, self.known, limit=candidate_count(n)
        )
        choice = choose_words(self.generate.select_words, self.topic, candidates, n)
        lexemes = [lex for c in choice.words if (lex := self.index.lookup(c.analysis)) is not None]
        self.pre_teach_shortfall = choice.shortfall
        self.known |= {lex.analysis for lex in lexemes}
        self.focus = lexemes
        self.taught += lexemes
        self._pre_taught_unlogged = lexemes
        return [self._lesson(lex, met_in=None) for lex in lexemes]

    def open(self) -> TutorTurn:
        """The tutor's first message."""
        focus = [lex.lemma for lex in self.focus]
        return self._turn(opening_message(self.topic, focus), learner_text=None)

    def respond(self, text: str) -> TutorTurn:
        return self._turn(text, learner_text=text)

    def end(self, learner_text: str | None = None) -> Ending:
        """End the conversation: the tutor's goodbye, then the stats and the tutor's notes.

        `learner_text` is the learner's own goodbye ("¡Hasta luego!"): a normal turn, so
        its words are credited. None when the learner ended it another way (the app's
        button): the tutor says goodbye, and no words are credited, since the learner
        wrote none. The end time and the notes are stored; the stats are computed from
        the log (sql/queries/session_stats.sql). If the notes can't be written, the
        conversation still ends, with the stats.
        """
        if learner_text is None:
            turn = self._turn(GOODBYE_MESSAGE, learner_text=None, farewell=True)
        else:
            turn = self._turn(learner_text, learner_text=learner_text, farewell=True)
        with self.conn:
            db.end_session(self.conn, self.session_id)
        self.ended = True
        stats = progress.session_stats(self.conn, self.session_id)
        transcript = progress.transcript(self.conn, self.session_id)
        started = time.perf_counter()
        try:
            generation = self.generate.summarize(summary_prompt(self.topic, transcript, stats))
        # The notes are a bonus: never lose the ending (or its stats) over a failed request.
        except Exception as error:  # noqa: BLE001
            return Ending(turn, stats, None, [], notes_error=str(error))
        notes: SessionNotes = generation.reply
        with self.conn:
            db.add_summary(
                self.conn,
                self.session_id,
                notes.went_well_en,
                notes.work_on,
                self.model,
                input_tokens=generation.input_tokens,
                cache_read_tokens=generation.cache_read_tokens,
                output_tokens=generation.output_tokens,
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
        return Ending(turn, stats, notes.went_well_en, notes.work_on)

    def _turn(self, message: str, learner_text: str | None, farewell: bool = False) -> TutorTurn:
        started = time.perf_counter()
        learner_words, not_words = self._learner_words(learner_text) if learner_text else ({}, [])
        # Words the learner just used count as known for the reply (seed rule: p implies r).
        newly_known = {lex.analysis for lex in learner_words} - self.known

        known = self.known | newly_known
        examples = self._examples(f"{self.topic or ''} {learner_text or ''}".strip(), known)
        taught_names = [lex.lemma for lex in self.taught if lex not in self.focus]
        focus = [lex.lemma for lex in self.focus]
        note = turn_note(examples, taught_names, focus=focus)
        if farewell:
            note += "\n\n" + FAREWELL_NOTE
        messages = [
            self.system,
            *self.history,
            learner_message(message, cached=True),
            SystemMessage(note),
        ]
        first = self.generate(messages)
        draft_new = self._new_words(first.reply.reply_es, known)
        final, final_new, generations = first, draft_new, [first]
        if len(draft_new) > MAX_NEW_WORDS:
            avoid = [lex.lemma for lex in draft_new]
            note = turn_note(examples, taught_names, avoid=avoid, focus=focus)
            if farewell:
                note += "\n\n" + FAREWELL_NOTE
            final = self.generate([*messages[:-1], SystemMessage(note)])
            final_new = self._new_words(final.reply.reply_es, known)
            generations.append(final)
        reply = final.reply
        latency_ms = round((time.perf_counter() - started) * 1000)

        with self.conn:
            events: list[Event] = []
            if learner_text is not None:
                self.turn_no += 1
                learner_turn = db.add_turn(
                    self.conn, self.session_id, self.turn_no, "learner", learner_text
                )
                events += self._learner_events(learner_words, reply.misused, learner_turn)
            self.turn_no += 1
            tutor_turn = db.add_turn(
                self.conn,
                self.session_id,
                self.turn_no,
                "tutor",
                reply.reply_es,
                note_en=clean_note(reply.note_en),
                draft_out_of_bank=len(draft_new),
                final_out_of_bank=len(final_new),
                retried=int(len(generations) > 1),
                input_tokens=sum(g.input_tokens for g in generations),
                cache_read_tokens=sum(g.cache_read_tokens for g in generations),
                cache_write_5m_tokens=sum(g.cache_write_5m_tokens for g in generations),
                cache_write_1h_tokens=sum(g.cache_write_1h_tokens for g in generations),
                output_tokens=sum(g.output_tokens for g in generations),
                latency_ms=latency_ms,
            )
            self.known |= newly_known
            for lex in self._reply_words(reply.reply_es):
                if lex in final_new:
                    events.append(Event(lex.lexeme_id, "taught", SOURCE, turn_id=tutor_turn))
                else:
                    events.append(Event(lex.lexeme_id, "seen", SOURCE, turn_id=tutor_turn))
            # Pre-taught topic words: taught before the opening, logged with it.
            events += [
                Event(lex.lexeme_id, "taught", "pre_teach", turn_id=tutor_turn)
                for lex in self._pre_taught_unlogged
            ]
            self._pre_taught_unlogged = []
            log_events(self.conn, events)
            self.known |= {lex.analysis for lex in final_new}
            self.taught += final_new
        self.last_tutor_turn_id = tutor_turn

        self.history += [learner_message(message), SystemMessage(note), AIMessage(reply.reply_es)]
        self.last = TutorTurn(
            reply_es=reply.reply_es,
            reply_en=reply.reply_en,
            note_en=clean_note(reply.note_en),
            lessons=[self._lesson(lex, met_in=reply.reply_es) for lex in final_new],
            retried=len(generations) > 1,
            draft_out_of_bank=len(draft_new),
            not_words=not_words,
            used=[lex.lemma for lex in learner_words],
        )
        return self.last

    def translate(self, question: str, phrase: str) -> TranslationTurn:
        """Answer "¿cómo se dice <phrase>?", pausing the conversation for one turn.

        Every new word in the answer is taught: the learner asked for it, so the one-new-
        word limit doesn't apply. The question's own words get no `used` events, because
        the phrase is English. Both turns are marked kind='translation', so adherence
        metrics leave them out.
        """
        started = time.perf_counter()
        note = translation_note(phrase)
        # The same schema and prefix as a conversation reply, so the cache is shared.
        generation = self.generate(
            [
                self.system,
                *self.history,
                learner_message(question, cached=True),
                SystemMessage(note),
            ]
        )
        answer: TutorReply = generation.reply
        spanish = answer.reply_es
        new = self._new_words(spanish, self.known)
        explanation = clean_note(answer.note_en)
        with self.conn:
            self.turn_no += 1
            db.add_turn(
                self.conn, self.session_id, self.turn_no, "learner", question, kind="translation"
            )
            self.turn_no += 1
            tutor_turn = db.add_turn(
                self.conn,
                self.session_id,
                self.turn_no,
                "tutor",
                spanish,
                kind="translation",
                note_en=explanation,
                input_tokens=generation.input_tokens,
                cache_read_tokens=generation.cache_read_tokens,
                cache_write_5m_tokens=generation.cache_write_5m_tokens,
                cache_write_1h_tokens=generation.cache_write_1h_tokens,
                output_tokens=generation.output_tokens,
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
            log_events(
                self.conn,
                [
                    Event(
                        lex.lexeme_id,
                        "taught" if lex in new else "seen",
                        SOURCE,
                        turn_id=tutor_turn,
                    )
                    for lex in self._reply_words(spanish)
                ],
            )
            self.known |= {lex.analysis for lex in new}
            self.taught += new
        self.last_tutor_turn_id = tutor_turn
        self.history += [learner_message(question), SystemMessage(note), AIMessage(spanish)]
        return TranslationTurn(
            spanish=spanish,
            explanation_en=explanation,
            lessons=[self._lesson(lex, met_in=spanish) for lex in new],
            pending=self.last.reply_es if self.last else None,
        )

    def look_up(self, word: str) -> Lesson | None:
        """The learner asks what a word means: a free reminder if it's known, else `taught`.

        Looking up a known word logs nothing: the learner shouldn't be penalized for
        using a reminder (Jason, 2026-10-05; until then it logged a grade-1 `looked_up`).
        An unknown word is taught, as anywhere else.

        A dictionary form is matched directly (index.headword); an inflected form
        ("nada") goes through the analyzer. Returns None for anything that isn't a word
        the lexicon or Wiktionary knows.
        """
        lex = self.index.headword(normalize_text(word))
        if lex is None:
            lexemes = [
                found
                for _, analyses in self.analyze(word)
                for analysis in analyses
                if (found := self.index.resolve(analysis)) is not None
            ]
            if not lexemes:
                return None
            lex = lexemes[0]
        if lex.analysis not in self.known:
            with self.conn:
                log_events(
                    self.conn,
                    [Event(lex.lexeme_id, "taught", SOURCE, turn_id=self.last_tutor_turn_id)],
                )
            self.known.add(lex.analysis)
            self.taught.append(lex)
        return self._lesson(lex, met_in=self.last.reply_es if self.last else None)

    # --- Helpers ---

    def _learner_words(self, text: str) -> tuple[dict[Lexeme, set[str]], list[str]]:
        """Lexemes in the learner's text, each with the surface forms used; and the tokens
        that aren't words at all (misspellings the lexicon and Wiktionary don't know)."""
        words: dict[Lexeme, set[str]] = defaultdict(set)
        not_words = []
        for surface, analyses in self.analyze(text):
            for analysis in analyses:
                if (lex := self.index.resolve(analysis)) is not None:
                    words[lex].add(surface)
                else:
                    not_words.append(surface)
        return dict(words), not_words

    def _learner_events(
        self, words: dict[Lexeme, set[str]], misused: list[Misuse], turn_id: int
    ) -> list[Event]:
        """`used` events for the learner's words, graded by Claude's misuse flags.

        A wrong form ("luchan" for luchar) is still the word the learner meant: credited,
        with a lower grade. A wrong word ("jugo" for juego) isn't: it never enters the
        production bank (Jason, 2026-10-05), and if the learner didn't know it, it isn't
        taught either. If it's already in the production bank, the lower grade is logged
        as a familiarity signal. Claude sometimes flags a phrase ("soy cansado"): every
        word in it counts.
        """

        def words_of(flags: list[Misuse]) -> set[str]:
            return {
                normalize_text(word).strip(PUNCTUATION)
                for flag in flags
                for word in flag.written.split()
            }

        wrong_words = words_of([m for m in misused if m.wrong_word])
        wrong_forms = words_of([m for m in misused if not m.wrong_word])
        events = []
        for lex, phrases in words.items():
            surfaces = {word for phrase in phrases for word in phrase.split()}  # expressions
            if surfaces & wrong_words:
                if self._produced(lex):
                    events.append(Event(lex.lexeme_id, "used", SOURCE, GRADE_MISUSED, turn_id))
                continue
            grade = GRADE_MISUSED if surfaces & wrong_forms else GRADE_USED
            events.append(Event(lex.lexeme_id, "used", SOURCE, grade, turn_id))
            if lex.analysis not in self.known:
                events.append(Event(lex.lexeme_id, "taught", SOURCE, turn_id=turn_id))
        return events

    def _produced(self, lex: Lexeme) -> bool:
        """Whether the word is in the production bank."""
        return (
            self.conn.execute(
                "SELECT 1 FROM word_bank WHERE lexeme_id = ? AND mode = 'production'",
                (lex.lexeme_id,),
            ).fetchone()
            is not None
        )

    def _reply_words(self, text: str) -> list[Lexeme]:
        """Distinct real words in a reply, in order. Non-words (tagger junk) are dropped."""
        found = {}
        for _, analyses in self.analyze(text):
            for analysis in analyses:
                if (lex := self.index.resolve(analysis)) is not None:
                    found.setdefault(lex, None)
        return list(found)

    def _new_words(self, text: str, known: set[Analysis]) -> list[Lexeme]:
        return [lex for lex in self._reply_words(text) if lex.analysis not in known]

    def _examples(self, query: str, known: set[Analysis]) -> list[str]:
        if self.store is None or not query:
            return []
        hits = search_sentences(self.store, query, known, k=EXAMPLES_PER_TURN, max_unknown=0)
        if len(hits) < 2:
            hits = search_sentences(self.store, query, known, k=EXAMPLES_PER_TURN, max_unknown=1)
        return [hit.es for hit in hits]

    def _vocab_of(self, text: str) -> set[Analysis]:
        return {analysis for _, analyses in self.analyze(text) for analysis in analyses}

    def _lesson(self, lex: Lexeme, met_in: str | None) -> Lesson:
        return lesson(self.conn, lex, self.known, self._vocab_of, self.store, met_in)


# --- Command line ----------------------------------------------------------------------

HELP = """Comandos:
  /q palabra   what does this word mean?
  /en          the last reply in English
  /palabras    words taught this session
  /salir       end the conversation (or say goodbye: "¡Hasta luego!")
  ¿Cómo se dice "..."?   how to say something in Spanish (pauses the conversation)
Accents: 'a -> á, ~n -> ñ, :u -> ü, ?palabra -> ¿palabra, !palabra -> ¡palabra"""


def format_lesson(item: Lesson) -> str:
    pos = POS_NAMES.get(item.pos, item.pos).rstrip("s")
    lines = [f"  + {item.lemma} ({pos}): {item.definition_en or '(no definition)'}"]
    if example := item.example:
        lines.append(f"      {example.es}")
        if example.en:
            lines.append(f"      {example.en}")
        if example.source and example.source.startswith("tatoeba:"):
            credit = f"Tatoeba #{example.source.split(':', 1)[1]}"
            lines.append(f"      ({credit}{', ' + example.author if example.author else ''})")
        for lemma, gloss in example.glosses:
            lines.append(f"      {lemma}: {gloss}")
    return "\n".join(lines)


def show(turn: TutorTurn) -> None:
    print(f"\nTutor: {turn.reply_es}")
    if turn.note_en:
        print(f"  Note: {turn.note_en}")
    if turn.not_words:
        print(f"  (Not recognized as Spanish words: {', '.join(turn.not_words)})")
    if turn.lessons:
        print("  New words:")
        for item in turn.lessons:
            print(format_lesson(item))


def show_translation(turn: TranslationTurn) -> None:
    print(f"\nTutor: {turn.spanish}")
    if turn.explanation_en:
        print(f"  {turn.explanation_en}")
    if turn.lessons:
        print("  New words:")
        for item in turn.lessons:
            print(format_lesson(item))
    if turn.pending:
        print(f"\n  (Seguimos: {turn.pending})")


def show_ending(ending: Ending) -> None:
    show(ending.turn)
    stats = ending.stats
    print(f"\n--- Resumen ({stats['minutes']:.0f} min) ---")
    print(
        f"  {stats['messages']} messages, {stats['corrections']} corrections, "
        f"{stats['how_to_say']} ¿cómo se dice?, {stats['words_used']} words used, "
        f"{stats['words_taught']} taught"
    )
    if stats["first_time"]:
        print(f"  Used for the first time: {', '.join(stats['first_time'])}")
    if stats["pre_taught"]:
        unused = [w for w in stats["pre_taught"] if w not in stats["pre_taught_used"]]
        print(
            f"  Today's words used: {len(stats['pre_taught_used'])} of {len(stats['pre_taught'])}"
        )
        if unused:
            print(f"  Not used yet: {', '.join(unused)}")
    if ending.went_well_en:
        print(f"\n  Went well: {ending.went_well_en}")
        print("  Work on:")
        for point in ending.work_on:
            print(f"    - {point}")
    elif ending.notes_error:
        print(f"\n  (The tutor's notes couldn't be written: {ending.notes_error})")


def written(text: str) -> str:
    """The learner's text with typed accent markers expanded, echoed when they changed it."""
    expanded = expand_markers(text)
    if expanded != text:
        print(f"  (escrito: {expanded})")
    return expanded


@dataclass
class Resources:
    """What every session shares: slow to load, so loaded once (by the CLI or the server)."""

    conn: sqlite3.Connection
    generate: ReplyGenerator
    index: LexiconIndex
    analyze: Callable[[str], list[TokenAnalysis]]
    store: Chroma | None


def load_resources(check_same_thread: bool = True) -> Resources:
    """Load the database, tagger, Wiktionary and embeddings (~20 s).

    The web server passes check_same_thread=False: its requests run on worker threads,
    and it serializes every use of the connection with a lock.
    """
    from spanish_tutor import lexicon
    from spanish_tutor.vectorstore import open_store

    conn = db.connect(check_same_thread=check_same_thread)
    if pending := db.pending_migrations(conn):
        print(f"Upgrading the database (migrations {pending}); backup: {db.backup()}")
    db.init_schema(conn)
    corrector = lexicon.load_corrector()

    def analyze(text: str) -> list[TokenAnalysis]:
        return next(lexicon.analyze([text], corrector=corrector))

    return Resources(
        conn,
        ClaudeGenerator(),
        LexiconIndex(conn, lexicon.load_wiktionary()),
        analyze,
        store=open_store(),
    )


def new_tutor(resources: Resources, topic: str | None) -> Tutor:
    return Tutor(
        resources.conn,
        resources.generate,
        resources.index,
        resources.analyze,
        store=resources.store,
        topic=topic,
    )


def reply_to(tutor: Tutor, text: str) -> tuple[str, TutorTurn | TranslationTurn | Ending]:
    """Route one learner message: (the text as written, the tutor's turn or the ending).

    "¿Cómo se dice ...?" is checked on the raw text, because its quoted phrase is English
    and must not have accent markers expanded. Otherwise the markers are expanded ('a ->
    á), and a message ending with a goodbye ends the conversation; anything else is a
    conversation turn.
    """
    if (phrase := parse_translation_request(text)) is not None:
        return text, tutor.translate(text, phrase)
    written = expand_markers(text)
    if is_farewell(written):
        return written, tutor.end(written)
    return written, tutor.respond(written)


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat in Spanish inside your vocabulary.")
    parser.add_argument("--topic", help='e.g. "el jardín" or "the weather"')
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    # Asked before loading, so the ~20 s load happens once the learner has answered.
    topic = args.topic or written(input("¿De qué quieres hablar? (Enter: de lo que quieras) "))
    topic = topic.strip() or None
    new_words = 0
    if topic:
        new_words = word_count(
            input(f"¿Cuántas palabras nuevas? ({MIN_WORDS}-{MAX_WORDS}, Enter = 5) ")
        )
    print("Cargando…")
    tutor = new_tutor(load_resources(), topic)
    print(HELP)
    lessons = tutor.pre_teach(new_words) if new_words else []
    if lessons:
        print("\nPalabras para hoy (try to use them in your replies):")
        for item in lessons:
            print(format_lesson(item))
    if tutor.pre_teach_shortfall:
        print(f"  ({len(lessons)} of {new_words} words. {tutor.pre_teach_shortfall})")
    show(tutor.open())
    while True:
        try:
            text = input("\nTú: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text in ("/salir", "/quit"):
            show_ending(tutor.end())
            break
        if text == "/en":
            print(f"  {tutor.last.reply_en if tutor.last else ''}")
        elif text == "/palabras":
            print("  " + (", ".join(lex.lemma for lex in tutor.taught) or "none yet"))
        elif text.startswith("/q "):
            found = tutor.look_up(written(text[3:].strip()))
            print(format_lesson(found) if found else "  Not a word I know.")
        elif text.startswith("/"):
            print(HELP)
        elif (phrase := parse_translation_request(text)) is not None:
            # Checked before markers are expanded: the quoted phrase is English.
            show_translation(tutor.translate(text, phrase))
        elif is_farewell(goodbye := written(text)):
            show_ending(tutor.end(goodbye))
            break
        else:
            show(tutor.respond(goodbye))
    print("¡Hasta luego!")


if __name__ == "__main__":
    main()
