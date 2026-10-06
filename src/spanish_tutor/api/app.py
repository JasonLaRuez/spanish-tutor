"""The tutor as a JSON API, plus the built web UI (web/dist) when it exists.

    uv run spanish-tutor serve        # http://127.0.0.1:8000, API docs at /docs

One learner, one process. The slow resources (tagger, Wiktionary, embeddings) load once
at startup. Active conversations live in memory, keyed by session id: a restart ends
them, though their transcripts stay in the database and are shown read-only.

Threads: FastAPI runs these (sync) endpoints on worker threads. The conversations share
one database connection and the lexicon index, so every use of them holds `lock`. The
other endpoints (progress, history, recommendations, the reading log) open their own
connection per request instead, so they answer immediately even while a reply (~6 s of
model time) is being generated. None of them calls the model.
"""

import sqlite3
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractContextManager, asynccontextmanager, closing
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from spanish_tutor import content, db, progress, recommend
from spanish_tutor.config import DB_PATH
from spanish_tutor.conversation import (
    Ending,
    Resources,
    TranslationTurn,
    Tutor,
    TutorTurn,
    clean_note,
    load_resources,
    new_tutor,
    reply_to,
)
from spanish_tutor.keyboard import expand_markers
from spanish_tutor.reading import STUDY_BATCH, ReadingSession
from spanish_tutor.teaching import Lesson
from spanish_tutor.topics import MAX_WORDS, MIN_WORDS

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


# --- Response and request models (they define the OpenAPI schema the UI's types use) --


class ExampleOut(BaseModel):
    es: str
    en: str | None
    source: str | None
    author: str | None
    glosses: list[tuple[str, str]]


class LessonOut(BaseModel):
    lexeme_id: int
    lemma: str
    pos: str
    definition_en: str | None
    example: ExampleOut | None
    model_written: bool  # the definition came from a model (resolve.py), not a dictionary

    @classmethod
    def of(cls, item: Lesson) -> "LessonOut":
        return cls.model_validate(asdict(item))


class TurnOut(BaseModel):
    """A tutor reply. For a "¿cómo se dice?" answer, kind is "translation", reply_es is
    the Spanish asked for, note_en its explanation, and pending the question the learner
    still has to answer."""

    kind: Literal["conversation", "translation"]
    reply_es: str
    reply_en: str | None
    note_en: str | None
    lessons: list[LessonOut]
    not_words: list[str] = []
    used: list[str] = []  # words (lemmas) the learner used in the message this replies to
    pending: str | None = None

    @classmethod
    def of(cls, turn: TutorTurn | TranslationTurn) -> "TurnOut":
        if isinstance(turn, TranslationTurn):
            return cls(
                kind="translation",
                reply_es=turn.spanish,
                reply_en=None,
                note_en=turn.explanation_en,
                lessons=[LessonOut.of(item) for item in turn.lessons],
                pending=turn.pending,
            )
        return cls(
            kind="conversation",
            reply_es=turn.reply_es,
            reply_en=turn.reply_en,
            note_en=turn.note_en,
            lessons=[LessonOut.of(item) for item in turn.lessons],
            not_words=turn.not_words,
            used=turn.used,
        )


class NewSession(BaseModel):
    topic: str | None = Field(None, description="What to talk about; none for open chat.")
    new_words: int = Field(
        0,
        ge=0,
        le=MAX_WORDS,
        description=f"Topic words to teach first: 0, or {MIN_WORDS}-{MAX_WORDS}.",
    )


class SessionStarted(BaseModel):
    session_id: int
    topic: str | None
    lessons: list[LessonOut]  # the topic words taught before the conversation
    requested_words: int  # how many topic words the learner asked for
    shortfall: str | None  # why fewer than requested were taught, for the learner
    opening: TurnOut


class Message(BaseModel):
    text: str = Field(min_length=1)


class SummaryOut(BaseModel):
    """The end-of-conversation summary: stats computed from the log, and the tutor's notes."""

    minutes: float | None
    messages: int
    how_to_say: int
    corrections: int
    words_used: int
    words_taught: int
    first_time: list[str]  # words the learner used for the first time ever
    pre_taught: list[str]  # today's topic words
    pre_taught_used: list[str]  # ... the ones the learner used
    went_well_en: str | None  # the tutor's notes; None if they couldn't be written
    work_on: list[str]
    notes_error: str | None = None

    @classmethod
    def of(cls, stats: dict, notes: dict | None, error: str | None = None) -> "SummaryOut":
        fields = {key: stats[key] for key in cls.model_fields if key in stats}
        return cls(
            **fields,
            went_well_en=notes["went_well_en"] if notes else None,
            work_on=notes["work_on"] if notes else [],
            notes_error=error,
        )

    @classmethod
    def of_ending(cls, ending: Ending) -> "SummaryOut":
        notes = (
            {"went_well_en": ending.went_well_en, "work_on": ending.work_on}
            if ending.went_well_en
            else None
        )
        return cls.of(ending.stats, notes, ending.notes_error)


class MessageReply(BaseModel):
    written: str  # the learner's text as sent, with accent markers expanded ('a -> á)
    turn: TurnOut
    taught: list[str]  # every word taught this session so far
    summary: SummaryOut | None = None  # set when the message ended the conversation (a goodbye)


class Ended(BaseModel):
    turn: TurnOut  # the tutor's goodbye
    summary: SummaryOut


class LookUp(BaseModel):
    word: str = Field(min_length=1)


class Band(BaseModel):
    band: int
    label: str
    words: int
    recognized: int
    produced: int


class Growth(BaseModel):
    session_id: int | None  # None: the seed
    topic: str | None
    started_at: str | None
    mode: Literal["recognition", "production"]
    words_added: int
    running_total: int


class GapWord(BaseModel):
    lemma: str
    pos: str
    definition_en: str | None
    frequency_per_million: float | None


class Progress(BaseModel):
    recognition: int
    production: int
    bands: list[Band]
    growth: list[Growth]
    try_using: list[GapWord]


class SessionSummary(BaseModel):
    session_id: int
    skill: str
    topic: str | None
    started_at: str
    last_at: str | None
    messages: int
    how_to_say: int
    corrections: int
    words_taught: int
    words_used: int
    ended_at: str | None  # None: still open, or left without ending
    active: bool  # still open in this server, so it can be continued


class TranscriptTurn(BaseModel):
    turn_no: int
    role: Literal["learner", "tutor"]
    kind: Literal["conversation", "translation", "study", "reading", "attempt"]
    text_es: str
    note_en: str | None
    created_at: str
    taught: list[str]
    pre_taught: list[str]  # topic words taught before the conversation (on the opening turn)
    used: list[str]


class Transcript(BaseModel):
    session: SessionSummary
    turns: list[TranscriptTurn]
    summary: SummaryOut | None  # for an ended conversation


# --- Content and recommendations (Phase 3) ---------------------------------------------


class RecommendedItem(BaseModel):
    content_id: int
    kind: Literal["song", "story"]
    title: str
    author: str | None
    new_words: int  # distinct words it would teach
    tokens: int  # running words counted as vocabulary
    unknown_share: float  # share of running words not known yet
    coverage: float | None  # share of running words already known


class RecommendedBook(BaseModel):
    book_id: int
    title: str
    author: str | None
    state: Literal["new", "in progress"]
    chapters: int
    density: float | None  # unknown running words / all, across the whole book
    new_words: int  # distinct new words in the whole book
    next_content_id: int
    next_chapter_no: int
    next_chapter_title: str
    next_chapter_new_words: int


class Recommendations(BaseModel):
    """Two rankings; the first of each is the default suggestion. Items with more than
    max_unknown_share of their running words unknown are listed apart, never suggested."""

    items: list[RecommendedItem]
    books: list[RecommendedBook]
    too_hard_items: list[RecommendedItem]
    too_hard_books: list[RecommendedBook]
    max_unknown_share: float


class Pick(BaseModel):
    content_id: int
    kind: Literal["song", "story", "chapter"]
    title: str
    new_words: int


class CatalogItem(BaseModel):
    content_id: int
    kind: Literal["song", "story", "chapter"]
    title: str
    author: str | None
    book_id: int | None
    book_title: str | None
    chapter_no: int | None
    indexed: bool
    new_words: int | None  # None until indexed
    tokens: int | None
    coverage: float | None
    state: Literal["started", "finished"] | None


class NewWord(BaseModel):
    lexeme_id: int
    lemma: str
    pos: str
    definition_en: str | None
    occurrences: int  # in this item
    model_written: bool


class ContentDetail(BaseModel):
    content_id: int
    kind: Literal["song", "story", "chapter"]
    title: str
    author: str | None
    source: str | None
    book_id: int | None
    book_title: str | None
    chapter_no: int | None
    chapters: int  # in its book; 0 for a song or story
    tokens: int | None
    unresolved_tokens: int | None
    indexed: bool
    started: bool
    finished: bool
    text_es: str
    new_words: list[NewWord]  # most frequent in the item first


class StartReading(BaseModel):
    chosen_via: Literal["recommended", "requested"] = Field(
        description="recommended: the default suggestion or a surprise; requested: the "
        "learner's own choice."
    )


# --- Reading (Phase 4) -----------------------------------------------------------------


class StartReadingSession(BaseModel):
    content_id: int
    chosen_via: Literal["recommended", "requested"]


class ReadingState(BaseModel):
    """Where a reading session stands: the text, and how much of it is studied."""

    session_id: int
    content_id: int
    kind: Literal["song", "story", "chapter"]
    title: str
    author: str | None
    book_title: str | None
    chapter_no: int | None
    chapters: int
    paragraphs: list[list[str]]  # paragraphs (stanzas) of sentences (lines), as analyzed
    total_new: int  # words new to the learner when the session started
    remaining: int  # ... still to study
    readable_until: int  # sentences, from the start, with every word studied or known
    unstudied: list[str]  # surface forms still to study, to mark in the text
    finished: bool


class StudyWord(BaseModel):
    lexeme_id: int
    lesson: LessonOut
    context: str  # the sentence where it first appears


class StudyBatch(BaseModel):
    words: list[StudyWord]
    state: ReadingState


class StudyRequest(BaseModel):
    lexeme_ids: list[int]


class ReadingLookUp(BaseModel):
    lesson: LessonOut
    state: ReadingState


# --- The app ----------------------------------------------------------------------------


def create_app(
    load: Callable[[], Resources] = lambda: load_resources(check_same_thread=False),
    db_path: Path | str = DB_PATH,
    web_dist: Path = WEB_DIST,
) -> FastAPI:
    """The app. Tests pass their own `load` (a scripted model, a temporary database)."""
    tutors: dict[int, Tutor] = {}
    readings: dict[int, ReadingSession] = {}
    lock = threading.Lock()
    state: dict[str, Resources] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state["resources"] = load()
        yield
        state["resources"].conn.close()

    app = FastAPI(title="Spanish tutor", lifespan=lifespan)

    def reader() -> AbstractContextManager[sqlite3.Connection]:
        return closing(db.connect(db_path))

    def tutor_for(session_id: int) -> Tutor:
        if (tutor := tutors.get(session_id)) is None:
            raise HTTPException(
                404, "This conversation isn't open (the server may have restarted)."
            )
        return tutor

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/api/sessions")
    def start_session(request: NewSession) -> SessionStarted:
        topic = expand_markers((request.topic or "").strip()) or None
        if request.new_words and not MIN_WORDS <= request.new_words <= MAX_WORDS:
            raise HTTPException(422, f"new_words must be 0 or {MIN_WORDS}-{MAX_WORDS}.")
        with lock:
            tutor = new_tutor(state["resources"], topic)
            lessons = tutor.pre_teach(request.new_words) if topic and request.new_words else []
            opening = tutor.open()
            tutors[tutor.session_id] = tutor
        return SessionStarted(
            session_id=tutor.session_id,
            topic=topic,
            lessons=[LessonOut.of(item) for item in lessons],
            requested_words=request.new_words if topic else 0,
            shortfall=tutor.pre_teach_shortfall,
            opening=TurnOut.of(opening),
        )

    @app.post("/api/sessions/{session_id}/messages")
    def send_message(session_id: int, message: Message) -> MessageReply:
        tutor = tutor_for(session_id)
        with lock:
            written, turn = reply_to(tutor, message.text.strip())
            taught = [lex.lemma for lex in tutor.taught]
            if isinstance(turn, Ending):  # a goodbye ended it
                tutors.pop(session_id, None)
                return MessageReply(
                    written=written,
                    turn=TurnOut.of(turn.turn),
                    taught=taught,
                    summary=SummaryOut.of_ending(turn),
                )
        return MessageReply(written=written, turn=TurnOut.of(turn), taught=taught)

    @app.post("/api/sessions/{session_id}/end")
    def end_session(session_id: int) -> Ended:
        """End the conversation without a typed goodbye (the "Hasta luego" button)."""
        tutor = tutor_for(session_id)
        with lock:
            ending = tutor.end()
            tutors.pop(session_id, None)
        return Ended(turn=TurnOut.of(ending.turn), summary=SummaryOut.of_ending(ending))

    @app.post("/api/sessions/{session_id}/lookup")
    def look_up(session_id: int, request: LookUp) -> LessonOut:
        tutor = tutor_for(session_id)
        with lock:
            found = tutor.look_up(request.word.strip())
        if found is None:
            raise HTTPException(404, f"{request.word!r} isn't a word I know.")
        return LessonOut.of(found)

    @app.get("/api/sessions")
    def list_sessions() -> list[SessionSummary]:
        with reader() as conn:
            return [
                SessionSummary(**row, active=row["session_id"] in tutors)
                for row in progress.sessions(conn)
            ]

    @app.get("/api/sessions/{session_id}")
    def get_transcript(session_id: int) -> Transcript:
        with reader() as conn:
            summary = next(
                (row for row in progress.sessions(conn) if row["session_id"] == session_id), None
            )
            if summary is None:
                raise HTTPException(404, "No such session.")
            turns = progress.transcript(conn, session_id)
            ended = None
            if summary["ended_at"]:
                stats = progress.session_stats(conn, session_id)
                ended = SummaryOut.of(stats, progress.summary(conn, session_id))
        return Transcript(
            summary=ended,
            session=SessionSummary(**summary, active=session_id in tutors),
            turns=[
                TranscriptTurn(
                    turn_no=t["turn_no"],
                    role=t["role"],
                    kind=t["kind"],
                    text_es=t["text_es"],
                    note_en=clean_note(t["note_en"]),
                    created_at=t["created_at"],
                    taught=t["taught"].split(", ") if t["taught"] else [],
                    pre_taught=t["pre_taught"].split(", ") if t["pre_taught"] else [],
                    used=t["used"].split(", ") if t["used"] else [],
                )
                for t in turns
            ],
        )

    @app.get("/api/progress")
    def get_progress(try_using: int = 20) -> Progress:
        with reader() as conn:
            counts = progress.words_by_mode(conn)
            return Progress(
                recognition=counts["recognition"],
                production=counts["production"],
                bands=progress.coverage_by_band(conn),
                growth=progress.growth_by_session(conn),
                try_using=progress.try_using(conn, try_using),
            )

    def reading_for(session_id: int) -> ReadingSession:
        if (session := readings.get(session_id)) is None:
            raise HTTPException(
                404, "This reading session isn't open (the server may have restarted)."
            )
        return session

    def reading_state(session: ReadingSession) -> ReadingState:
        item = session.item
        return ReadingState(
            session_id=session.session_id,
            content_id=session.content_id,
            kind=item["kind"],
            title=item["title"],
            author=item["author"],
            book_title=item["book_title"],
            chapter_no=item["chapter_no"],
            chapters=item["chapters"],
            paragraphs=session.paragraphs,
            total_new=len(session.new_words),
            remaining=len(session.remaining),
            readable_until=session.readable_until(),
            unstudied=sorted(session.unstudied_forms()),
            finished=session.finished,
        )

    @app.post("/api/reading")
    def start_reading_session(request: StartReadingSession) -> ReadingState:
        """Start reading an item: analyzes its text (a few seconds for a long chapter)."""
        resources = state["resources"]
        with lock:
            try:
                session = ReadingSession(
                    resources.conn,
                    resources.generate,
                    resources.index,
                    resources.analyze,
                    request.content_id,
                    request.chosen_via,
                    store=resources.store,
                )
            except KeyError as error:
                raise HTTPException(404, "No such song, story or chapter.") from error
            readings[session.session_id] = session
            return reading_state(session)

    @app.get("/api/reading/{session_id}")
    def get_reading(session_id: int) -> ReadingState:
        return reading_state(reading_for(session_id))

    @app.get("/api/reading/{session_id}/batch")
    def next_batch(session_id: int, n: int = STUDY_BATCH) -> StudyBatch:
        session = reading_for(session_id)
        with lock:
            batch = session.next_batch(n)
            return StudyBatch(
                words=[
                    StudyWord(
                        lexeme_id=w.lexeme.lexeme_id, lesson=LessonOut.of(lesson), context=w.context
                    )
                    for w, lesson in batch
                ],
                state=reading_state(session),
            )

    @app.post("/api/reading/{session_id}/study")
    def study(session_id: int, request: StudyRequest) -> ReadingState:
        session = reading_for(session_id)
        with lock:
            session.study(request.lexeme_ids)
            return reading_state(session)

    @app.post("/api/reading/{session_id}/lookup")
    def reading_look_up(session_id: int, request: LookUp) -> ReadingLookUp:
        session = reading_for(session_id)
        with lock:
            found = session.look_up(request.word)
            if found is None:
                raise HTTPException(404, f"“{request.word}” isn't a word the dictionary knows.")
            return ReadingLookUp(lesson=LessonOut.of(found), state=reading_state(session))

    @app.post("/api/reading/{session_id}/finish")
    def finish_reading_session(session_id: int) -> ReadingState:
        session = reading_for(session_id)
        with lock:
            session.finish()
            return reading_state(session)

    @app.post("/api/reading/{session_id}/discuss")
    def discuss(session_id: int) -> SessionStarted:
        """Talk about the text: the conversation tutor, continuing the reading session. Its
        messages and ending then go through the conversation endpoints."""
        session = reading_for(session_id)
        if not session.finished:
            raise HTTPException(409, "Finish reading first, then talk about it.")
        resources = state["resources"]
        with lock:
            if session_id not in tutors:
                store = resources.store
                tutor = session.discuss(store.embeddings if store is not None else None)
                opening = tutor.open()
                tutors[session_id] = tutor
            else:
                tutor = tutors[session_id]
                opening = tutor.last
        return SessionStarted(
            session_id=session_id,
            topic=tutor.topic,
            lessons=[],
            requested_words=0,
            shortfall=None,
            opening=TurnOut.of(opening),
        )

    @app.get("/api/recommend")
    def get_recommendations(limit: int = 10) -> Recommendations:
        with reader() as conn:
            return Recommendations(
                items=recommend.items(conn, limit),
                books=recommend.books(conn),
                too_hard_items=recommend.items(conn, limit, too_hard=True),
                too_hard_books=recommend.books(conn, too_hard=True),
                max_unknown_share=recommend.MAX_UNKNOWN_SHARE,
            )

    @app.get("/api/recommend/surprise")
    def get_surprise() -> Pick:
        with reader() as conn:
            pick = recommend.surprise(conn)
        if pick is None:
            raise HTTPException(404, "There's nothing to read yet: add a song, story or book.")
        return Pick(**pick)

    @app.get("/api/content")
    def list_content() -> list[CatalogItem]:
        with reader() as conn:
            return [CatalogItem(**row) for row in recommend.catalog(conn)]

    @app.get("/api/content/{content_id}")
    def get_content(content_id: int) -> ContentDetail:
        with reader() as conn:
            detail = recommend.item(conn, content_id)
            if detail is None:
                raise HTTPException(404, "No such song, story or chapter.")
            return ContentDetail(**detail, new_words=recommend.new_words(conn, content_id))

    @app.post("/api/content/{content_id}/start")
    def start_reading(content_id: int, request: StartReading) -> dict:
        with reader() as conn:
            if recommend.item(conn, content_id) is None:
                raise HTTPException(404, "No such song, story or chapter.")
            content.start(conn, content_id, request.chosen_via)
        return {"ok": True}

    @app.post("/api/content/{content_id}/finish")
    def finish_reading(content_id: int) -> dict:
        with reader() as conn:
            if recommend.item(conn, content_id) is None:
                raise HTTPException(404, "No such song, story or chapter.")
            content.finish(conn, content_id)
        return {"ok": True}

    # The built web UI. Any other path gets index.html, so the UI's own routes
    # (/history/3) work on reload.
    if (web_dist / "index.html").exists():

        @app.get("/{path:path}", include_in_schema=False)
        def web(path: str) -> FileResponse:
            file = (web_dist / path).resolve()
            if path and file.is_file() and file.is_relative_to(web_dist.resolve()):
                return FileResponse(file)
            return FileResponse(web_dist / "index.html")

    return app


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn

    print("Loading the tutor (about 20 s) ...")
    uvicorn.run(create_app(), host=host, port=port)
