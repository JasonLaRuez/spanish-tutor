"""The reading skill (roadmap Phase 4, slice 4.2; Jason's design, 2026-10-05).

A reading session is one song, story or chapter:

1. **Study.** Every word in the text the learner doesn't know yet is taught, in batches
   (STUDY_BATCH), in the order the words first appear, so each batch covers the next
   stretch of the text. Each batch is a `study` turn carrying its `taught` events (source
   'reading'), logged when the learner studies it.
2. **Read**, at any time. Words not yet studied are marked, and any word can be looked up:
   an unknown one is taught as a one-word study turn, a known one is a free reminder.
3. **Finished:** a `reading` turn carries a `seen` event for every known word in the text
   (an encounter), and the item is finished in content_events.
4. **Talk about it** (offered, not automatic): the conversation tutor, continuing the same
   session, with the text's passages retrieved into every turn's note (Tutor.passages).

The text is analyzed once, when the session starts, and each word is resolved to its
lexeme the way the indexer resolved it (content.index_item): the lexicon, a dictionary
word, or an earlier decision for that form (word_resolutions). Forms with none are left
out, as they are in the index.
"""

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import InMemoryVectorStore

from spanish_tutor import content, db, english, recommend
from spanish_tutor.config import MODEL
from spanish_tutor.conversation import INSTRUCTIONS, ReplyGenerator, Tutor
from spanish_tutor.english import is_english
from spanish_tutor.lexicon import Analysis, TokenAnalysis, normalize_text
from spanish_tutor.teaching import Lesson, lesson
from spanish_tutor.words import Event, Lexeme, LexiconIndex, log_events

SOURCE = "reading"
STUDY_BATCH = 20
PASSAGE_SENTENCES = 3  # sentences (or song lines) per retrievable passage
# A text this short (about 3,000 tokens) is given to the discussion whole instead of by
# retrieved passages (Jason, 2026-10-07). Measured in slice 4.4e: the 3 passages held the
# answer to only 5 of 10 questions about 400-word chapters. 2,000 words covers every
# chapter of An Elementary Spanish Reader (at most 1,753), every poem (706) and about
# half of Quiroga's stories (median 2,237); it adds at most ~2.4¢ of cache writing to a
# discussion, then cheap cache reads.
WHOLE_TEXT_WORDS = 2000
READING_INSTRUCTIONS = INSTRUCTIONS + (Path(__file__).parent / "prompts" / "reading.md").read_text(
    encoding="utf-8"
)


@dataclass(frozen=True)
class NewWord:
    """A word of the text the learner didn't know when the session started."""

    lexeme: Lexeme
    first: int  # position of its first occurrence among the text's running words
    sentence: int  # the sentence (or song line) it first appears in
    context: str  # that sentence


class TextPassages:
    """The text cut into passages of a few sentences, embedded in memory for retrieval.

    Built when a discussion starts (a long chapter takes a second or two on CPU), with the
    same embedding model as the Tatoeba store.
    """

    def __init__(self, units: list[str], embeddings: Embeddings, size: int = PASSAGE_SENTENCES):
        self.passages = [" ".join(units[i : i + size]) for i in range(0, len(units), size)]
        self.store = InMemoryVectorStore(embeddings)
        if self.passages:
            self.store.add_texts(self.passages)

    def search(self, query: str, k: int = 3) -> list[str]:
        if not query or not self.passages:
            return []
        return [doc.page_content for doc in self.store.similarity_search(query, k=k)]


class ReadingSession:
    """One reading session: study, read, finish, and optionally talk about the text.

    `skill` is both the session's skill and its events' source; the lyrics skill
    (lyrics.LyricsSession) is a reading session of a song or poem with skill 'lyrics'.
    """

    skill = SOURCE

    def __init__(
        self,
        conn: sqlite3.Connection,
        generate: ReplyGenerator,
        index: LexiconIndex,
        analyze: Callable[[str], list[TokenAnalysis]],
        content_id: int,
        chosen_via: str,
        *,
        store: Chroma | None = None,
        analyze_many: Callable[[list[str]], Iterable[list[TokenAnalysis]]] | None = None,
        model: str = MODEL,
    ):
        item = recommend.item(conn, content_id)
        if item is None:
            raise KeyError(f"no content item {content_id}")
        self.conn, self.generate, self.index, self.analyze = conn, generate, index, analyze
        self.store, self.model = store, model
        self.item = item
        self.content_id = content_id
        # Paragraphs (a song's stanzas) of sentences (a song's lines), for the reader page;
        # the units are those sentences in order, as the indexer analyzed them.
        self.paragraphs = content.paragraphs(item["text_es"], item["kind"])
        self.units = [unit for paragraph in self.paragraphs for unit in paragraph]
        self.known: set[Analysis] = db.known_vocabulary(conn)
        self.turn_no = 0
        self.finished = False
        with conn:
            self.session_id = db.start_session(conn, self.skill, model, item["title"])
        content.start(conn, content_id, chosen_via, self.session_id)

        analyze_many = analyze_many or (lambda units: [analyze(u) for u in units])
        # A song's marked words per line (english.py): its English words are skipped like
        # names; its English loanwords are ordinary words, only shown marked.
        self.marks = english.load_marks(conn, content_id)
        self.english = {
            n: {w for w, kind in words.items() if kind == "english"}
            for n, words in self.marks.items()
        }
        # Per sentence: (surface form, its lexeme or None), and every new word in order.
        self.tokens: list[list[tuple[str, Lexeme | None]]] = []
        self.new_words: list[NewWord] = []
        seen_new: set[int] = set()
        position = 0
        for number, (unit, tokens) in enumerate(
            zip(self.units, analyze_many(self.units), strict=True)
        ):
            resolved = []
            for form, analyses in tokens:
                if is_english(form, self.english.get(number + 1)):
                    resolved.append((form, None))
                    continue
                for lemma, pos in analyses:
                    lex = self._lexeme(form, (lemma, pos))
                    resolved.append((form, lex))
                    new = lex is not None and lex.analysis not in self.known
                    if new and lex.lexeme_id not in seen_new:
                        seen_new.add(lex.lexeme_id)
                        self.new_words.append(NewWord(lex, position, number, unit))
                    position += 1
            self.tokens.append(resolved)

    # --- What's left to study ---

    @property
    def remaining(self) -> list[NewWord]:
        """New words not studied (or looked up) yet, in order of first appearance."""
        return [w for w in self.new_words if w.lexeme.analysis not in self.known]

    def next_batch(self, n: int = STUDY_BATCH) -> list[tuple[NewWord, Lesson]]:
        """The next n words to study, each with its lesson. Logs nothing."""
        return [(w, self._lesson(w.lexeme, w.context)) for w in self.remaining[:n]]

    def study(self, lexeme_ids: Iterable[int]) -> list[Lexeme]:
        """Teach these words (from the remaining ones; others are ignored), as one study turn."""
        wanted = set(lexeme_ids)
        batch = [w.lexeme for w in self.remaining if w.lexeme.lexeme_id in wanted]
        if batch:
            self._teach(batch)
        return batch

    def unstudied_forms(self) -> set[str]:
        """The surface forms (lowercase words) of the text's words still to study."""
        forms = set()
        for sentence in self.tokens:
            for form, lex in sentence:
                if lex is not None and lex.analysis not in self.known:
                    forms.update(form.split())  # an expression: each of its words
        return forms

    def readable_until(self) -> int:
        """How many sentences, from the start, have every word studied or known."""
        for number, sentence in enumerate(self.tokens):
            if any(lex is not None and lex.analysis not in self.known for _, lex in sentence):
                return number
        return len(self.tokens)

    # --- Reading ---

    def look_up(self, word: str) -> Lesson | None:
        """A word clicked in the text: taught if unknown (a one-word study turn), else a
        free reminder. None for anything that isn't a word, or is one of the text's English
        words (english.py)."""
        if self.only_english(word):
            return None
        lex = self.index.headword(normalize_text(word))
        if lex is None:
            found = [
                resolved
                for form, analyses in self.analyze(word)
                for analysis in analyses
                if (resolved := self._lexeme(form, analysis)) is not None
            ]
            if not found:
                return None
            lex = found[0]
        if lex.analysis not in self.known:
            self._teach([lex])
        context = next((w.context for w in self.new_words if w.lexeme == lex), None)
        return self._lesson(lex, context)

    def only_english(self, word: str) -> bool:
        """Whether a word appears in the text only as English: marked English on every line
        that has it."""
        word = normalize_text(word)
        lines = [n for n, unit in enumerate(self.units, 1) if word in english.words_of(unit)]
        return bool(lines) and all(word in self.english.get(n, set()) for n in lines)

    def marked_words(self) -> list[dict[str, str]]:
        """Per sentence (line): its marked words and their kind, for the reader page."""
        return [self.marks.get(n, {}) for n in range(1, len(self.units) + 1)]

    def finish(self) -> None:
        """Finished reading: `seen` for every known word in the text, and the item finished."""
        if self.finished:
            return
        known_lexemes = {
            lex.lexeme_id: lex
            for sentence in self.tokens
            for _, lex in sentence
            if lex is not None and lex.analysis in self.known
        }
        with self.conn:
            turn = self._add_turn("learner", "reading", self.item["title"])
            log_events(
                self.conn,
                [Event(lex_id, "seen", self.skill, turn_id=turn) for lex_id in known_lexemes],
            )
            db.end_session(self.conn, self.session_id)
        content.finish(self.conn, self.content_id, self.session_id)
        self.finished = True

    def discuss(self, embeddings: Embeddings | None) -> Tutor:
        """The conversation about the text, continuing this session (call after finish).

        `embeddings` embed the text's passages for retrieval; without them (no embedding
        model loaded) the tutor still talks about the text, from its title alone.
        """
        title = self.item["title"]
        # A short text goes into the prompt whole (cached with the rest of it), and nothing
        # is retrieved; a long one is searched for the passages closest to each message.
        whole = len(self.item["text_es"].split()) <= WHOLE_TEXT_WORDS
        return Tutor(
            self.conn,
            self.generate,
            self.index,
            self.analyze,
            store=self.store,
            topic=f"«{title}»",
            model=self.model,
            skill=self.skill,
            source=self.skill,
            session_id=self.session_id,
            first_turn_no=self.turn_no,
            opening=(
                f"[The learner has just finished reading «{title}». Greet them and ask a "
                "simple first question about it.]"
            ),
            instructions=READING_INSTRUCTIONS + (self.whole_text() if whole else ""),
            passages=TextPassages(self.units, embeddings) if embeddings and not whole else None,
        )

    def whole_text(self) -> str:
        """The text for the discussion's prompt: paragraphs (stanzas) apart, a song's lines
        on lines of their own."""
        joiner = "\n" if self.item["kind"] in content.VERSE else " "
        body = "\n\n".join(joiner.join(paragraph) for paragraph in self.paragraphs)
        return f"\n\n## The text: «{self.item['title']}»\n\n{body}\n"

    # --- Helpers ---

    def _lexeme(self, form: str, analysis: Analysis) -> Lexeme | None:
        """The lexeme a word resolves to, as the indexer resolved it (no new rows)."""
        if (lex := self.index.find(analysis)) is not None:
            return lex
        if self.index.in_dictionary(analysis):
            return self.index.resolve(analysis)
        found, lexeme_id = content.latest_resolution(self.conn, (form, *analysis))
        if not found or lexeme_id is None:
            return None
        row = self.conn.execute(
            "SELECT lemma, pos FROM lexemes WHERE lexeme_id = ?", (lexeme_id,)
        ).fetchone()
        return Lexeme(lexeme_id, row[0], row[1])

    def _teach(self, lexemes: list[Lexeme]) -> None:
        with self.conn:
            turn = self._add_turn("tutor", "study", ", ".join(lex.lemma for lex in lexemes))
            log_events(
                self.conn,
                [Event(lex.lexeme_id, "taught", self.skill, turn_id=turn) for lex in lexemes],
            )
        self.known |= {lex.analysis for lex in lexemes}

    def _add_turn(self, role: str, kind: str, text: str, **columns: object) -> int:
        self.turn_no += 1
        return db.add_turn(
            self.conn, self.session_id, self.turn_no, role, text, kind=kind, **columns
        )

    def _vocab_of(self, text: str) -> set[Analysis]:
        return {analysis for _, analyses in self.analyze(text) for analysis in analyses}

    def _lesson(self, lex: Lexeme, met_in: str | None) -> Lesson:
        return lesson(self.conn, lex, self.known, self._vocab_of, self.store, met_in)
