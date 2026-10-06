"""The conversation benchmark (slice 4.4c): scripted learner messages, the real tutor.

The real log fills slowly, so the benchmark gives numbers now and a regression test for
later prompt changes. benchmark.json holds 8 topics with 5 learner messages each: 24 carry
one planted mistake labeled as the grader classifies it (conversation.Misuse: wrong form
or wrong word), 16 are correct Spanish.

Each run copies a frozen snapshot of the word bank (benchmark-base.db, made once from the
real one, so later runs face the same vocabulary), then for each topic pre-teaches,
opens, and sends the 5 messages through the real tutor, exactly as the app does. What it
measures:

- adherence and completeness: the log metrics (metrics.py) on the run's database copy;
- grading accuracy: Claude's misuse flags, which the database doesn't keep, are recorded
  per message (a Recorder around the generator) and scored against the planted mistakes:
  detected, classified correctly (form vs word), and false alarms on correct words;
- the words the replies taught go to the rating queue (eval_items, `new_word_flag`) in
  the real database, for Jason to say whether each was really new to him (the new-word
  detector's precision).

Everything a run makes stays local (data/processed/eval/benchmark/<run>/, gitignored):
the database copy, results.jsonl (one row per message) and summary.json (scores, cost).

    python -m spanish_tutor.evaluation freeze                 # the snapshot, once
    python -m spanish_tutor.evaluation benchmark --pilot      # the first topic only
    python -m spanish_tutor.evaluation benchmark              # all 8
    python -m spanish_tutor.evaluation score <run directory>
"""

import json
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.messages import BaseMessage

from spanish_tutor import db
from spanish_tutor.config import DATA_DIR, DB_PATH, MODEL
from spanish_tutor.conversation import (
    PUNCTUATION,
    Generation,
    ReplyGenerator,
    Resources,
    load_resources,
    new_tutor,
)
from spanish_tutor.evaluation.metrics import Rate, report
from spanish_tutor.lexicon import normalize_text
from spanish_tutor.topics import TopicWords

SCRIPT = Path(__file__).with_name("benchmark.json")
EVAL_DIR = DATA_DIR / "processed" / "eval"
BASE_DB = EVAL_DIR / "benchmark-base.db"
RUNS_DIR = EVAL_DIR / "benchmark"

# Claude Opus 5.5, per million tokens (the claude-api skill, 2026-09-25): input $4, output
# $20; cache writes 1.25x (5-minute) or 2x (1-hour), cache reads 0.05x.
PRICE_IN, PRICE_OUT = 4.0, 20.0


# --- The script -------------------------------------------------------------------------


@dataclass(frozen=True)
class PlantedError:
    written: str  # the mistaken text, as it appears in the message
    intended: str
    kind: str  # wrong_form | wrong_word
    type: str  # gender, conjugation, ser/estar, English word...


@dataclass(frozen=True)
class Message:
    text: str
    error: PlantedError | None = None


@dataclass(frozen=True)
class Topic:
    topic: str
    messages: list[Message]


@dataclass(frozen=True)
class Script:
    version: int
    pre_teach: int
    topics: list[Topic]


def load_script(path: Path = SCRIPT) -> Script:
    raw = json.loads(path.read_text(encoding="utf-8"))
    topics = [
        Topic(
            t["topic"],
            [
                Message(m["text"], PlantedError(**m["error"]) if m.get("error") else None)
                for m in t["messages"]
            ],
        )
        for t in raw["topics"]
    ]
    return Script(raw["version"], raw["pre_teach"], topics)


# --- Recording what the model said --------------------------------------------------------


class Recorder:
    """A ReplyGenerator that passes every request on and keeps each Generation: the misuse
    flags of each reply (not stored in the database) and every call's tokens, including
    the topic-word choice, whose cost the turns table doesn't record."""

    def __init__(self, inner: ReplyGenerator):
        self.inner = inner
        self.calls: list[tuple[str, Generation]] = []

    def __call__(self, messages: list[BaseMessage]) -> Generation:
        generation = self.inner(messages)
        self.calls.append(("reply", generation))
        return generation

    def select_words(self, prompt: str) -> TopicWords:
        ask = getattr(self.inner, "ask", None)
        if ask is None:
            return self.inner.select_words(prompt)
        generation = ask(TopicWords, prompt)  # the same call, with its token counts
        self.calls.append(("select", generation))
        return generation.reply

    def summarize(self, prompt: str) -> Generation:
        generation = self.inner.summarize(prompt)
        self.calls.append(("summary", generation))
        return generation


def cents(generation: Generation) -> float:
    """One call's cost in cents, from its token counts."""
    g = generation
    plain = g.input_tokens - g.cache_write_5m_tokens - g.cache_write_1h_tokens
    dollars = (
        plain * PRICE_IN
        + g.cache_write_5m_tokens * PRICE_IN * 1.25
        + g.cache_write_1h_tokens * PRICE_IN * 2
        + g.cache_read_tokens * PRICE_IN * 0.05
        + g.output_tokens * PRICE_OUT
    ) / 1e6
    return dollars * 100


# --- Running --------------------------------------------------------------------------------


def copy_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    src, dst = sqlite3.connect(source), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()


def freeze_base(source: Path = DB_PATH, target: Path = BASE_DB, force: bool = False) -> Path:
    """The snapshot every run starts from: made once, so runs face the same vocabulary."""
    if target.exists() and not force:
        return target
    copy_database(source, target)
    return target


def run_topic(resources: Resources, recorder: Recorder, topic: Topic, pre_teach: int) -> list[dict]:
    tutor = new_tutor(resources, topic.topic)
    tutor.pre_teach(pre_teach)
    tutor.open()
    results = []
    for number, message in enumerate(topic.messages, start=1):
        before = len(recorder.calls)
        turn = tutor.respond(message.text)
        final = [g for kind, g in recorder.calls[before:] if kind == "reply"][-1]
        results.append(
            {
                "topic": topic.topic,
                "message_no": number,
                "session_id": tutor.session_id,
                "text": message.text,
                "error": asdict(message.error) if message.error else None,
                "flags": [m.model_dump() for m in final.reply.misused],
                "reply_es": turn.reply_es,
                "note_en": turn.note_en,
                "retried": turn.retried,
                "draft_out_of_bank": turn.draft_out_of_bank,
                "taught": [lesson.lemma for lesson in turn.lessons],
                "used": turn.used,
            }
        )
    return results


def run(
    topics: Iterable[str] | None = None,
    base: Path = BASE_DB,
    runs_dir: Path = RUNS_DIR,
    load: Callable[[Path], Resources] = lambda path: load_resources(db_path=path),
    script: Script | None = None,
) -> Path:
    """Run the benchmark (all topics, or those named) on a fresh copy of `base`."""
    script = script or load_script()
    wanted = set(topics) if topics is not None else None
    chosen = [t for t in script.topics if wanted is None or t.topic in wanted]
    run_dir = runs_dir / datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
    copy = run_dir / "benchmark.db"
    copy_database(base, copy)

    resources = load(copy)
    recorder = Recorder(resources.generate)
    resources = replace(resources, generate=recorder)
    known = len(db.known_vocabulary(resources.conn))
    # The copy also holds the real history: the run's own sessions start after it.
    first_session = resources.conn.execute(
        "SELECT COALESCE(MAX(session_id), 0) + 1 FROM sessions"
    ).fetchone()[0]
    with resources.conn:
        resources.conn.execute(
            "INSERT INTO eval_runs (kind, model, prompt_version, config) "
            "VALUES ('benchmark', ?, ?, ?)",
            (
                MODEL,
                f"benchmark.json v{script.version}",
                json.dumps(
                    {
                        "topics": [t.topic for t in chosen],
                        "pre_teach": script.pre_teach,
                        "known_words": known,
                        "base": str(base),
                    }
                ),
            ),
        )
    results = []
    for topic in chosen:
        results += run_topic(resources, recorder, topic, script.pre_teach)
    resources.conn.close()

    (run_dir / "results.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results), encoding="utf-8"
    )
    summary = {
        "topics": [t.topic for t in chosen],
        "known_words": known,
        "first_session": first_session,
        "calls": len(recorder.calls),
        "cents": round(sum(cents(g) for _, g in recorder.calls), 2),
        "cents_by_kind": {
            kind: round(sum(cents(g) for k, g in recorder.calls if k == kind), 2)
            for kind in sorted({k for k, _ in recorder.calls})
        },
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return run_dir


# --- Scoring the grading ------------------------------------------------------------------


def tokens(text: str) -> set[str]:
    return {t for word in normalize_text(text).split() if (t := word.strip(PUNCTUATION))}


def matching_flag(error: dict, flags: list[dict]) -> dict | None:
    """The flag that points at the planted mistake: one sharing a word with it."""
    planted = tokens(error["written"])
    return next((f for f in flags if tokens(f["written"]) & planted), None)


def score_grading(results: list[dict]) -> dict:
    """Claude's misuse flags against the planted mistakes.

    detected: planted mistakes with a flag on them. classified: of those, flagged as the
    right kind (wrong_word True for a wrong word, False for a wrong form). False alarms:
    flags on anything else, on clean messages and beside a planted mistake.
    """
    with_error = [r for r in results if r["error"]]
    clean = [r for r in results if not r["error"]]
    detected, classified, misses = [], [], []
    by_kind: dict[str, list[int]] = {}
    false_alarms = []
    for r in with_error:
        flag = matching_flag(r["error"], r["flags"])
        kind = r["error"]["kind"]
        counts = by_kind.setdefault(kind, [0, 0, 0])  # detected, classified, planted
        counts[2] += 1
        if flag is None:
            misses.append(r)
        else:
            detected.append(r)
            counts[0] += 1
            if flag["wrong_word"] == (kind == "wrong_word"):
                classified.append(r)
                counts[1] += 1
            else:
                misses.append(r)
        planted = tokens(r["error"]["written"])  # a second flag on the mistake isn't an alarm
        false_alarms += [
            {**f, "text": r["text"]} for f in r["flags"] if not tokens(f["written"]) & planted
        ]
    false_alarms += [{**f, "text": r["text"]} for r in clean for f in r["flags"]]
    return {
        "messages": len(results),
        "detected": Rate(len(detected), len(with_error)),
        "classified": Rate(len(classified), len(detected)),
        "clean_without_flags": Rate(sum(1 for r in clean if not r["flags"]), len(clean)),
        "by_kind": {
            kind: {"detected": Rate(d, n), "classified": Rate(c, d)}
            for kind, (d, c, n) in sorted(by_kind.items())
        },
        "false_alarms": false_alarms,
        "misses": misses,  # undetected or misclassified, for review
    }


def read_results(run_dir: Path) -> list[dict]:
    lines = (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


def score(run_dir: Path) -> dict:
    """Everything a run measured: grading, the log metrics on its copy, and the cost."""
    results = read_results(run_dir)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    conn = sqlite3.connect(f"file:{(run_dir / 'benchmark.db').as_posix()}?mode=ro", uri=True)
    try:
        metrics = report(conn, from_session=first_session(summary, results))
    finally:
        conn.close()
    return {"grading": score_grading(results), "metrics": metrics, "summary": summary}


def first_session(summary: dict, results: list[dict]) -> int:
    """Where the run's own sessions start in its copy (the first pilot didn't record it)."""
    return summary.get("first_session") or min(r["session_id"] for r in results)


def format_grading(grading: dict) -> str:
    lines = [
        f"Grading ({grading['messages']} learner messages)",
        f"  planted mistakes flagged:     {grading['detected']}",
        f"  ... as the right kind:        {grading['classified']}",
        f"  correct messages, no flags:   {grading['clean_without_flags']}",
    ]
    for kind, rates in grading["by_kind"].items():
        lines.append(f"  {kind:10} flagged {rates['detected']}; right kind {rates['classified']}")
    for miss in grading["misses"]:
        error = miss["error"]
        flags = (
            ", ".join(
                f"{f['written']}{' (word)' if f['wrong_word'] else ' (form)'}"
                for f in miss["flags"]
            )
            or "no flags"
        )
        lines.append(f"    miss: {miss['text']}  [{error['written']}: {error['kind']}]  -> {flags}")
    for alarm in grading["false_alarms"]:
        kind = "word" if alarm["wrong_word"] else "form"
        lines.append(f"    false alarm: {alarm['written']} ({kind}) in: {alarm['text']}")
    return "\n".join(lines)


# --- The rating queue -----------------------------------------------------------------------


def queue_new_word_flags(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    origin: str,
    from_session: int | None = None,
) -> int:
    """Put every word a tutor reply taught (in `source`) on the rating queue (in `target`):
    was it really new to the learner? `origin` prefixes the item's source_ref ('' for the
    real log, 'benchmark:<run>:' for a run); `from_session` keeps only a run's own
    sessions. Returns how many were added."""
    found = source.execute(
        """
        SELECT t.turn_id, t.text_es, s.topic, l.lexeme_id, l.lemma, l.pos, l.definition_en
        FROM word_events AS e
        JOIN turns AS t ON t.turn_id = e.turn_id
        JOIN sessions AS s ON s.session_id = t.session_id
        JOIN lexemes AS l ON l.lexeme_id = e.lexeme_id
        WHERE e.event_type = 'taught' AND e.source <> 'pre_teach'
          AND t.role = 'tutor' AND t.kind = 'conversation'
          AND (:from_session IS NULL OR t.session_id >= :from_session)
        ORDER BY t.turn_id, l.lemma
        """,
        {"from_session": from_session},
    ).fetchall()
    added = 0
    with target:
        for turn_id, text, topic, lexeme_id, lemma, pos, definition in found:
            content = {
                "lemma": lemma,
                "pos": pos,
                "definition_en": definition,
                "sentence": text,
                "topic": topic,
            }
            added += target.execute(
                "INSERT OR IGNORE INTO eval_items (item_type, source_ref, content) "
                "VALUES ('new_word_flag', ?, ?)",
                (
                    f"{origin}turn:{turn_id}:lexeme:{lexeme_id}",
                    json.dumps(content, ensure_ascii=False),
                ),
            ).rowcount
    return added
