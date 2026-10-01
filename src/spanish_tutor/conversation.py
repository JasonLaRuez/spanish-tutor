"""The conversation skill: chat in Spanish inside the learner's vocabulary.

    uv run python -m spanish_tutor.conversation [--topic "el jardín"]

Each turn:
1. The learner's message is analyzed. Every real word in it is logged as `used`
   (production); words Claude flags as misused get a lower grade. A word the learner
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
the session starts). Per-turn context (examples, words taught this session) goes in a
system note after each learner message, so it never invalidates the cached prefix.
"""

import argparse
import sqlite3
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from langchain_chroma import Chroma
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from spanish_tutor import db
from spanish_tutor.config import MODEL
from spanish_tutor.lexicon import Analysis, TokenAnalysis, normalize_text
from spanish_tutor.teaching import Lesson, lesson
from spanish_tutor.vectorstore import search_sentences
from spanish_tutor.words import (
    GRADE_LOOKED_UP,
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


class TutorReply(BaseModel):
    """The structured reply Claude returns each turn."""

    reply_es: str = Field(description="Your reply to the learner, in Spanish only.")
    reply_en: str = Field(description="A natural English translation of reply_es.")
    misused: list[str] = Field(
        description="Words from the learner's last message that were used wrongly, "
        "exactly as the learner wrote them. Empty if none."
    )
    correction_en: str | None = Field(
        description="A short English note on the most important mistake, with the "
        "corrected Spanish; null if there were no mistakes."
    )


@dataclass(frozen=True)
class Generation:
    reply: TutorReply
    input_tokens: int = 0  # not read from the cache (includes cache writes)
    cache_read_tokens: int = 0
    output_tokens: int = 0  # includes thinking


class ReplyGenerator(Protocol):
    def __call__(self, messages: list[BaseMessage]) -> Generation: ...


class ClaudeGenerator:
    """Claude via LangChain, returning a TutorReply.

    Opus 5.5 rejects sampling parameters and forced tool calls, so: no temperature, and
    structured output via `json_schema` (output_config.format) rather than LangChain's
    default forced-tool method. `effort="low"` suits short chat turns.

    Server-side refusal fallback is on: if a safety classifier declines a request (a
    false positive is the only plausible case for a language tutor), the API reruns it
    on a fallback model instead of returning nothing.
    """

    def __init__(self, model: str = MODEL, effort: str = "low", max_tokens: int = 4000):
        from langchain_anthropic import ChatAnthropic

        llm = ChatAnthropic(
            model=model,
            effort=effort,
            max_tokens=max_tokens,
            betas=["server-side-fallback-2026-07-01"],
            model_kwargs={"fallbacks": "default"},
        )
        self.chain = llm.with_structured_output(TutorReply, method="json_schema", include_raw=True)

    def __call__(self, messages: list[BaseMessage]) -> Generation:
        result = self.chain.invoke(messages)
        if result["parsing_error"] is not None:
            raise result["parsing_error"]
        usage = result["raw"].usage_metadata or {}
        cache_read = (usage.get("input_token_details") or {}).get("cache_read", 0) or 0
        return Generation(
            reply=result["parsed"],
            input_tokens=usage.get("input_tokens", 0) - cache_read,
            cache_read_tokens=cache_read,
            output_tokens=usage.get("output_tokens", 0),
        )


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
            # The breakpoint caches instructions + vocabulary: the whole stable prefix.
            {
                "type": "text",
                "text": vocabulary_block(known),
                "cache_control": {"type": "ephemeral"},
            },
        ]
    )


def turn_note(examples: list[str], taught: list[str], avoid: list[str] | None = None) -> str:
    parts = []
    if examples:
        parts.append("Sentences the learner can read:\n" + "\n".join(f"- {s}" for s in examples))
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


def opening_message(topic: str | None) -> str:
    if topic:
        return f"[The learner has started a conversation about: {topic}. Greet them and begin.]"
    return "[The learner has started a conversation. Greet them and ask what they'd like to talk about.]"


# --- The skill -------------------------------------------------------------------------


@dataclass
class TutorTurn:
    """What the learner sees after a turn."""

    reply_es: str
    reply_en: str
    correction_en: str | None
    lessons: list[Lesson]
    retried: bool
    draft_out_of_bank: int
    not_words: list[str] = field(default_factory=list)  # learner tokens that aren't words


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
        # Live recognition set: grows as words are taught or used during the session.
        self.known = db.known_vocabulary(conn)
        # Frozen at session start, so the cached prompt prefix never changes.
        self.system = system_message(self.known)
        self.history: list[BaseMessage] = []
        self.taught: list[Lexeme] = []
        self.turn_no = 0
        self.last_tutor_turn_id: int | None = None
        self.last: TutorTurn | None = None
        with conn:
            self.session_id = db.start_session(conn, "conversation", model, topic)

    # --- Turns ---

    def open(self) -> TutorTurn:
        """The tutor's first message."""
        return self._turn(opening_message(self.topic), learner_text=None)

    def respond(self, text: str) -> TutorTurn:
        return self._turn(text, learner_text=text)

    def _turn(self, message: str, learner_text: str | None) -> TutorTurn:
        started = time.perf_counter()
        learner_words, not_words = self._learner_words(learner_text) if learner_text else ({}, [])
        # Words the learner just used count as known for the reply (seed rule: p implies r).
        newly_known = {lex.analysis for lex in learner_words} - self.known

        known = self.known | newly_known
        examples = self._examples(f"{self.topic or ''} {learner_text or ''}".strip(), known)
        taught_names = [lex.lemma for lex in self.taught]
        note = turn_note(examples, taught_names)
        messages = [self.system, *self.history, HumanMessage(message), SystemMessage(note)]
        first = self.generate(messages)
        draft_new = self._new_words(first.reply.reply_es, known)
        final, final_new, generations = first, draft_new, [first]
        if len(draft_new) > MAX_NEW_WORDS:
            note = turn_note(examples, taught_names, avoid=[lex.lemma for lex in draft_new])
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
                correction_en=reply.correction_en,
                draft_out_of_bank=len(draft_new),
                final_out_of_bank=len(final_new),
                retried=int(len(generations) > 1),
                input_tokens=sum(g.input_tokens for g in generations),
                cache_read_tokens=sum(g.cache_read_tokens for g in generations),
                output_tokens=sum(g.output_tokens for g in generations),
                latency_ms=latency_ms,
            )
            self.known |= newly_known
            for lex in self._reply_words(reply.reply_es):
                if lex in final_new:
                    events.append(Event(lex.lexeme_id, "taught", SOURCE, turn_id=tutor_turn))
                else:
                    events.append(Event(lex.lexeme_id, "seen", SOURCE, turn_id=tutor_turn))
            log_events(self.conn, events)
            self.known |= {lex.analysis for lex in final_new}
            self.taught += final_new
        self.last_tutor_turn_id = tutor_turn

        self.history += [HumanMessage(message), SystemMessage(note), AIMessage(reply.reply_es)]
        self.last = TutorTurn(
            reply_es=reply.reply_es,
            reply_en=reply.reply_en,
            correction_en=reply.correction_en,
            lessons=[self._lesson(lex, met_in=reply.reply_es) for lex in final_new],
            retried=len(generations) > 1,
            draft_out_of_bank=len(draft_new),
            not_words=not_words,
        )
        return self.last

    def look_up(self, word: str) -> Lesson | None:
        """The learner asks what a word means: `looked_up` if it's known, else `taught`.

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
        turn_id = self.last_tutor_turn_id
        if lex.analysis in self.known:
            event = Event(lex.lexeme_id, "looked_up", SOURCE, GRADE_LOOKED_UP, turn_id)
        else:
            event = Event(lex.lexeme_id, "taught", SOURCE, turn_id=turn_id)
        with self.conn:
            log_events(self.conn, [event])
        if lex.analysis not in self.known:
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
        self, words: dict[Lexeme, set[str]], misused: list[str], turn_id: int
    ) -> list[Event]:
        # Claude sometimes flags a phrase ("soy cansado"): every word in it counts.
        flagged = {
            normalize_text(word).strip(PUNCTUATION) for phrase in misused for word in phrase.split()
        }
        events = []
        for lex, surfaces in words.items():
            grade = GRADE_MISUSED if surfaces & flagged else GRADE_USED
            events.append(Event(lex.lexeme_id, "used", SOURCE, grade, turn_id))
            if lex.analysis not in self.known:
                events.append(Event(lex.lexeme_id, "taught", SOURCE, turn_id=turn_id))
        return events

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
  /salir       quit"""


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
    if turn.correction_en:
        print(f"  Note: {turn.correction_en}")
    if turn.not_words:
        print(f"  (Not recognized as Spanish words: {', '.join(turn.not_words)})")
    if turn.lessons:
        print("  New words:")
        for item in turn.lessons:
            print(format_lesson(item))


def build_tutor(topic: str | None) -> Tutor:
    """Load everything the session needs (~20 s: spaCy, Wiktionary, embeddings)."""
    from spanish_tutor import lexicon
    from spanish_tutor.vectorstore import open_store

    conn = db.connect()
    if pending := db.pending_migrations(conn):
        print(f"Upgrading the database (migrations {pending}); backup: {db.backup()}")
    db.init_schema(conn)
    corrector = lexicon.load_corrector()

    def analyze(text: str) -> list[TokenAnalysis]:
        return next(lexicon.analyze([text], corrector=corrector))

    return Tutor(
        conn,
        ClaudeGenerator(),
        LexiconIndex(conn, lexicon.load_wiktionary()),
        analyze,
        store=open_store(),
        topic=topic,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat in Spanish inside your vocabulary.")
    parser.add_argument("--topic", help='e.g. "el jardín" or "the weather"')
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print("Cargando…")
    tutor = build_tutor(args.topic)
    print(HELP)
    show(tutor.open())
    while True:
        try:
            text = input("\nTú: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text in ("/salir", "/quit"):
            break
        if text == "/en":
            print(f"  {tutor.last.reply_en if tutor.last else ''}")
        elif text == "/palabras":
            print("  " + (", ".join(lex.lemma for lex in tutor.taught) or "none yet"))
        elif text.startswith("/q "):
            found = tutor.look_up(text[3:].strip())
            print(format_lesson(found) if found else "  Not a word I know.")
        elif text.startswith("/"):
            print(HELP)
        else:
            show(tutor.respond(text))
    print("¡Hasta luego!")


if __name__ == "__main__":
    main()
