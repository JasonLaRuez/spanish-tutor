"""Translation naturalness (slice 4.4d): translate, judge repeatedly, measure the judges.

Before any judge-based number means anything, the judge must agree with itself. In slice
4.3 the app's attempt comparison called the same attempt "close" in one run and "right"
in another. So every judgment here is repeated, and the first result is consistency
(Krippendorff's alpha across repeats, agreement.py), not a score.

A run, on a copy of the frozen snapshot (benchmark.BASE_DB), because a lyrics session
logs the poem as started and that would skew the real log's recommendation metrics:

1. Translates the poems within the difficulty ceiling with the app's own lyrics code
   (LyricsSession.translation: Opus 5.5, medium effort), and queues every line in the
   real database's rating queue (eval_items, `translation_line`), for Jason's
   naturalness ratings later (the calibration).
2. The judge (Claude Sonnet 5.5, medium effort; Jason's choice of model) scores every
   line on naturalness and faithfulness, 1-5 (RUBRIC), one call per poem, REPEATS times.
3. The attempt verdicts: ATTEMPTS, written at a known quality (right, close, missed), go
   through the app's own comparison (LyricsSession.attempt: Opus 5.5, low effort), each
   REPEATS times: does it agree with itself, and with the intended verdict?

Judgments go in the real database's `ratings` (rater = the model, one row per repeat),
under an eval_runs row per judge. Run directory: data/processed/eval/translation/<run>/.

    python -m spanish_tutor.evaluation translations --pilot     # Rima XXIII, Rima XVII
    python -m spanish_tutor.evaluation translations
    python -m spanish_tutor.evaluation consistency RUN_ID [RUN_ID ...]
"""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from spanish_tutor import recommend
from spanish_tutor.config import MODEL
from spanish_tutor.conversation import ClaudeGenerator, Generation, Resources, load_resources
from spanish_tutor.evaluation.agreement import alpha
from spanish_tutor.evaluation.benchmark import BASE_DB, EVAL_DIR, Recorder, cents, copy_database
from spanish_tutor.lyrics import LyricsSession
from spanish_tutor.progress import rows

RUNS_DIR = EVAL_DIR / "translation"
JUDGE_MODEL = "claude-sonnet-5-5"
REPEATS = 5
# v2 (Jason, 2026-10-07): "supplying a word the Spanish implies is faithful" ended a 4/5
# wobble. v3 (read each line with its neighbours; don't judge sound) was tried on the 3
# calibration poems (judge run 5) and dropped: the judge grew stricter against Jason (mean
# 3.62 vs his 4.86; abs. difference 1.25 vs 1.00) and less consistent (faithfulness alpha
# 0.782). Poems aren't a fair calibration set (see CLAUDE.md, slice 4.4d).
PROMPT_VERSION = "translation-judge v2"

RUBRIC = """\
You are judging English translations of a Spanish poem, made for a language-learning app.
Each line has a "natural" translation, meant to say what the line means the way an
English speaker would say it. Read each line in the context of the whole poem (a line may
continue a sentence), then rate it on two scales.

Naturalness (the English alone): does it read like English a native speaker would write?
Poetic word order and register are fine.
  5: fluent and idiomatic; it could have been written in English.
  4: natural, with one small awkwardness.
  3: understandable, but clearly a translation: stiff, or shaped like the Spanish.
  2: awkward enough to slow a reader; unidiomatic in several places.
  1: not natural English: word for word, or ungrammatical.

Faithfulness (the Spanish against the English): does it keep the line's meaning?
English needs many words that Spanish leaves unsaid (a subject pronoun, an auxiliary, a
verb carried over from another line): supplying a word the Spanish implies is faithful,
not an addition.
  5: the full meaning, including figurative sense and tone; nothing added or lost.
  4: the meaning, with a small nuance lost or added.
  3: the main meaning, but a noticeable part lost, added or shifted.
  2: a significant mistranslation or omission.
  1: the meaning is wrong.

Give each line both scores (whole numbers from 1 to 5) and one short reason (under 20
words). Rate every line number exactly once.
"""


class JudgedLine(BaseModel):
    line_no: int
    naturalness: int = Field(description="1 to 5, by the rubric.")
    faithfulness: int = Field(description="1 to 5, by the rubric.")
    reason: str = Field(description="Under 20 words: what decided the scores.")


class TranslationJudgment(BaseModel):
    lines: list[JudgedLine]


@dataclass(frozen=True)
class Attempt:
    content_id: int
    line_no: int
    text: str
    intended: str  # right | close | missed


# The attempts (Jason reviewed them, 2026-10-06): for 6 lines of Rima XVII (56) and Rima
# XXXVIII (77), one each at a known quality. "missed" ones are typical learner errors: a
# false friend (suspiros / suspects), creo read as crear.
ATTEMPTS = [
    Attempt(56, 2, "Today the sun reaches the depths of my soul", "right"),
    Attempt(56, 2, "Today the sun arrives at my soul", "close"),
    Attempt(56, 2, "Today my soul goes to the bottom of the sun", "missed"),
    Attempt(56, 3, "Today I saw her... I saw her and she looked at me", "right"),
    Attempt(56, 3, "Today I saw her... I saw her", "close"),
    Attempt(56, 3, "Today she saw me and I looked away", "missed"),
    Attempt(56, 4, "Today I believe in God!", "right"),
    Attempt(56, 4, "I believe in God!", "close"),
    Attempt(56, 4, "Today I created God!", "missed"),
    Attempt(77, 1, "Sighs are air, and they return to the air", "right"),
    Attempt(77, 1, "Breaths are air and go into the air", "close"),
    Attempt(77, 1, "The suspects are air and they fly", "missed"),
    Attempt(77, 3, "Tell me, woman: when love is forgotten,", "right"),
    Attempt(77, 3, "Tell me, woman: when love forgets,", "close"),
    Attempt(77, 3, "Give me a woman when I forget love", "missed"),
    Attempt(77, 4, "Do you know where it goes?", "right"),
    Attempt(77, 4, "You know where it goes.", "close"),
    Attempt(77, 4, "Do you know when it comes back?", "missed"),
]
QUALITIES = ("right", "close", "missed")


@dataclass
class Poem:
    content_id: int
    title: str
    lines: list[str]  # Spanish, one per unit
    natural: list[str]
    literal: list[str]
    notes: list[str | None]


# --- The judge --------------------------------------------------------------------------


def judge_prompt(poem: Poem) -> str:
    listing = "\n".join(
        f"{n}. Spanish: {es}\n   English: {en}"
        for n, (es, en) in enumerate(zip(poem.lines, poem.natural, strict=True), 1)
    )
    return f"{RUBRIC}\n«{poem.title}»\n{listing}"


def valid_judgment(judgment: TranslationJudgment, lines: int) -> dict[int, JudgedLine] | None:
    """The judgment's lines by number, if it rated every line once with scores 1-5."""
    found = {line.line_no: line for line in judgment.lines}
    if sorted(found) != list(range(1, lines + 1)) or len(judgment.lines) != lines:
        return None
    if any(
        not (1 <= s <= 5) for line in found.values() for s in (line.naturalness, line.faithfulness)
    ):
        return None
    return found


def start_run(conn: sqlite3.Connection, model: str, prompt_version: str, config: dict) -> int:
    with conn:
        return conn.execute(
            "INSERT INTO eval_runs (kind, model, prompt_version, config) VALUES ('judge', ?, ?, ?)",
            (model, prompt_version, json.dumps(config, ensure_ascii=False)),
        ).lastrowid


def queue_item(conn: sqlite3.Connection, item_type: str, ref: str, content: dict) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO eval_items (item_type, source_ref, content) VALUES (?, ?, ?)",
        (item_type, ref, json.dumps(content, ensure_ascii=False)),
    )
    return conn.execute(
        "SELECT item_id FROM eval_items WHERE item_type = ? AND source_ref = ?", (item_type, ref)
    ).fetchone()[0]


def queue_poem(conn: sqlite3.Connection, run_name: str, poem: Poem) -> list[int]:
    """Every line of a translated poem on the rating queue; returns their item ids."""
    with conn:
        return [
            queue_item(
                conn,
                "translation_line",
                f"translation:{run_name}:{poem.content_id}:{n}",
                {
                    "poem": poem.title,
                    "line_no": n,
                    "spanish": poem.lines[n - 1],
                    "natural_en": poem.natural[n - 1],
                    "literal_en": poem.literal[n - 1],
                    "note_en": poem.notes[n - 1],
                },
            )
            for n in range(1, len(poem.lines) + 1)
        ]


def judge_poems(
    conn: sqlite3.Connection,
    poems: list[tuple[Poem, list[int]]],
    ask: Callable[..., Generation],
    repeats: int = REPEATS,
    model: str = JUDGE_MODEL,
    log: Callable[[str], None] = print,
) -> tuple[int, list[Generation]]:
    """Judge each poem's lines `repeats` times; returns the judge run and its calls."""
    run_id = start_run(conn, model, PROMPT_VERSION, {"repeats": repeats, "effort": "medium"})
    calls = []
    for repeat in range(1, repeats + 1):
        for poem, item_ids in poems:
            prompt = judge_prompt(poem)
            for attempt in range(2):  # one retry if a line is missing, doubled or off-scale
                generation = ask(TranslationJudgment, prompt)
                calls.append(generation)
                found = valid_judgment(generation.reply, len(poem.lines))
                if found:
                    break
            if not found:
                log(f"  {poem.title}, repeat {repeat}: no valid judgment, skipped")
                continue
            with conn:
                for n, item_id in enumerate(item_ids, 1):
                    line = found[n]
                    tokens = (
                        (generation.input_tokens, generation.output_tokens)
                        if n == 1
                        else (None, None)
                    )
                    for criterion, score in (
                        ("naturalness", line.naturalness),
                        ("faithfulness", line.faithfulness),
                    ):
                        conn.execute(
                            "INSERT INTO ratings (item_id, criterion, rater, run_id, repeat_no, score, "
                            "rationale, input_tokens, output_tokens) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                item_id,
                                criterion,
                                model,
                                run_id,
                                repeat,
                                score,
                                line.reason,
                                *tokens,
                            ),
                        )
                        tokens = (None, None)
    return run_id, calls


# --- The attempt verdicts ---------------------------------------------------------------


def judge_attempts(
    conn: sqlite3.Connection,
    sessions: dict[int, LyricsSession],
    repeats: int = REPEATS,
    attempts: list[Attempt] = ATTEMPTS,
    model: str = MODEL,
) -> int:
    """Each quality's attempts for a poem go through the app's comparison together (one
    call, as the app sends a learner's attempts), `repeats` times; returns the run."""
    run_id = start_run(
        conn, model, "lyrics.feedback_prompt (the app's)", {"repeats": repeats, "effort": "low"}
    )
    with conn:
        item_of = {
            a: queue_item(
                conn,
                "attempt",
                f"attempt:v1:{a.content_id}:{a.line_no}:{a.intended}",
                {
                    "poem": sessions[a.content_id].item["title"],
                    "line_no": a.line_no,
                    "spanish": sessions[a.content_id].units[a.line_no - 1],
                    "attempt": a.text,
                    "intended": a.intended,
                },
            )
            for a in attempts
        }
    for repeat in range(1, repeats + 1):
        for content_id, session in sessions.items():
            for quality in QUALITIES:
                group = [
                    a for a in attempts if a.content_id == content_id and a.intended == quality
                ]
                if not group:
                    continue
                compared = {
                    c.line_no: c for c in session.attempt({a.line_no: a.text for a in group})
                }
                with conn:
                    for a in group:
                        found = compared[a.line_no]
                        if found.verdict is None:
                            continue
                        conn.execute(
                            "INSERT INTO ratings (item_id, criterion, rater, run_id, repeat_no, "
                            "label, rationale) VALUES (?, 'verdict', ?, ?, ?, ?, ?)",
                            (item_of[a], model, run_id, repeat, found.verdict, found.comment_en),
                        )
    return run_id


# --- Consistency ------------------------------------------------------------------------


def consistency(conn: sqlite3.Connection, run_id: int) -> dict[str, dict]:
    """Per criterion of a judge run: how often the repeats agree, and alpha (ordinal for
    scores, nominal for verdicts). For verdicts, also agreement with the intended one."""
    found: dict[str, dict] = {}
    gathered = rows(conn, "eval_judge_consistency", run_id=run_id)
    for criterion in sorted({r["criterion"] for r in gathered}):
        items = [r for r in gathered if r["criterion"] == criterion]
        units = [json.loads(r["ratings"]) for r in items]
        scored = all(isinstance(v, (int, float)) for unit in units for v in unit)
        result = {
            "items": len(items),
            "repeats": max((len(u) for u in units), default=0),
            "all_agree": sum(1 for u in units if len(set(u)) == 1),
            "alpha": alpha(units, "ordinal" if scored else "nominal"),
        }
        if scored:
            result["within_one"] = sum(1 for u in units if max(u) - min(u) <= 1)
            result["mean"] = sum(sum(u) for u in units) / sum(len(u) for u in units)
        else:
            intended = [json.loads(r["content"]).get("intended") for r in items]
            pairs = [(v, want) for unit, want in zip(units, intended, strict=True) for v in unit]
            result["matches_intended"] = (sum(1 for v, want in pairs if v == want), len(pairs))
            result["by_intended"] = {
                quality: [
                    v
                    for unit, want in zip(units, intended, strict=True)
                    if want == quality
                    for v in unit
                ]
                for quality in QUALITIES
            }
        found[criterion] = result
    return found


def format_consistency(run_id: int, found: dict[str, dict]) -> str:
    lines = [f"Judge run {run_id}"]
    for criterion, r in found.items():
        a = "undefined" if r["alpha"] is None else f"{r['alpha']:.3f}"
        line = (
            f"  {criterion:12} {r['items']} items x {r['repeats']} repeats: alpha {a}; "
            f"all repeats agree on {r['all_agree']}/{r['items']}"
        )
        if "within_one" in r:
            line += f"; within 1 point {r['within_one']}/{r['items']}; mean {r['mean']:.2f}"
        else:
            k, n = r["matches_intended"]
            line += f"; matches the intended verdict {k}/{n}"
        lines.append(line)
        for quality, verdicts in r.get("by_intended", {}).items():
            counts = {v: verdicts.count(v) for v in QUALITIES if verdicts.count(v)}
            lines.append(f"    intended {quality:6} -> {counts}")
    return "\n".join(lines)


# --- Calibration: the judge against Jason -------------------------------------------------


def calibration(conn: sqlite3.Connection, run_id: int, criterion: str = "naturalness") -> dict:
    """A judge run against Jason's latest ratings on the same items. Each line's judge
    score is the median of its repeats. A stable judge can still be off: consistency says
    the judge agrees with itself, calibration says whether it agrees with the learner."""
    found = rows(conn, "eval_calibration", run_id=run_id, criterion=criterion)
    pairs = [(r["human"], median(json.loads(r["judge"])), r) for r in found]
    diffs = [h - j for h, j, _ in pairs]
    n = len(pairs)
    return {
        "items": n,
        "human_mean": sum(h for h, _, _ in pairs) / n if n else None,
        "judge_mean": sum(j for _, j, _ in pairs) / n if n else None,
        "exact": sum(1 for d in diffs if d == 0),
        "within_one": sum(1 for d in diffs if abs(d) <= 1),
        "judge_lower": sum(1 for d in diffs if d > 0),
        "judge_higher": sum(1 for d in diffs if d < 0),
        "mean_absolute_difference": sum(abs(d) for d in diffs) / n if n else None,
        "alpha": alpha([[h, j] for h, j, _ in pairs], "ordinal"),
        "differences": [
            (json.loads(r["content"]).get("natural_en"), h, json.loads(r["judge"]))
            for h, j, r in pairs
            if h != j
        ],
    }


def median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def format_calibration(run_id: int, found: dict) -> str:
    if not found["items"]:
        return f"Judge run {run_id}: no items Jason has rated"
    a = "undefined" if found["alpha"] is None else f"{found['alpha']:.2f}"
    summary = (
        f"Judge run {run_id} against Jason ({found['items']} lines): Jason's mean "
        f"{found['human_mean']:.2f}, the judge's {found['judge_mean']:.2f}; exact "
        f"{found['exact']}, within 1 point {found['within_one']}; judge lower on "
        f"{found['judge_lower']}, higher on {found['judge_higher']}; mean absolute "
        f"difference {found['mean_absolute_difference']:.2f}; alpha {a}"
    )
    lines = [summary]
    for text, human, judge in found["differences"]:
        lines.append(f"    Jason {human:g}, judge {[int(s) for s in judge]}: {text}")
    return "\n".join(lines)


def poems_from_queue(
    conn: sqlite3.Connection, translation_run: str
) -> list[tuple[Poem, list[int]]]:
    """A translation run's poems, rebuilt from their queued lines (to judge them again
    without translating again)."""
    found: dict[int, list[tuple[int, dict]]] = {}
    for item_id, ref, content in conn.execute(
        "SELECT item_id, source_ref, content FROM eval_items "
        "WHERE item_type = 'translation_line' AND source_ref LIKE ? ORDER BY item_id",
        (f"translation:{translation_run}:%",),
    ):
        content_id = int(ref.split(":")[2])
        found.setdefault(content_id, []).append((item_id, json.loads(content)))
    poems = []
    for content_id, lines in found.items():
        lines.sort(key=lambda pair: pair[1]["line_no"])
        poem = Poem(
            content_id,
            lines[0][1]["poem"],
            [x["spanish"] for _, x in lines],
            [x["natural_en"] for _, x in lines],
            [x["literal_en"] for _, x in lines],
            [x["note_en"] for _, x in lines],
        )
        poems.append((poem, [item_id for item_id, _ in lines]))
    return poems


def rejudge(
    conn: sqlite3.Connection,
    translation_run: str,
    titles: list[str] | None = None,
    ask: Callable[..., Generation] | None = None,
    repeats: int = REPEATS,
) -> tuple[int, float]:
    """Judge a translation run's lines again with the current rubric (all poems, or those
    named); returns the new judge run and its cost in cents."""
    poems = [
        p for p in poems_from_queue(conn, translation_run) if titles is None or p[0].title in titles
    ]
    recorder = Recorder(
        _Asker(ask or ClaudeGenerator(model=JUDGE_MODEL, effort="medium", max_tokens=8000).ask)
    )
    run_id, _ = judge_poems(conn, poems, recorder.ask, repeats)
    return run_id, round(sum(cents(g, JUDGE_MODEL) for _, g in recorder.calls), 2)


# --- Running ------------------------------------------------------------------------------


def run(
    pilot: bool = False,
    base: Path = BASE_DB,
    runs_dir: Path = RUNS_DIR,
    target: sqlite3.Connection | None = None,
    load: Callable[[Path], Resources] = lambda path: load_resources(db_path=path),
    translator: Callable[..., Generation] | None = None,
    judge: Callable[..., Generation] | None = None,
    repeats: int = REPEATS,
    log: Callable[[str], None] = print,
) -> dict:
    """The whole slice: translate, queue, judge, attempts. `target` is where items and
    ratings go (the real word bank by default); the lyrics sessions run on a copy."""
    from spanish_tutor import db

    run_dir = runs_dir / datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
    copy = run_dir / "translation.db"
    copy_database(base, copy)
    resources = load(copy)
    app = Recorder(resources.generate)  # the attempt comparison (low effort)
    translate = Recorder(_Asker(translator or ClaudeGenerator(effort="medium").ask))
    resources = replace(resources, generate=app)
    target = target or db.connect()
    db.init_schema(target)

    candidates = [i for i in recommend.items(resources.conn) if i["kind"] == "poem"]
    if pilot:
        wanted = {62, 56}  # Rima XXIII to judge, Rima XVII for the attempts
        candidates = [i for i in candidates if i["content_id"] in wanted]
    sessions: dict[int, LyricsSession] = {}
    poems: list[tuple[Poem, list[int]]] = []
    for item in candidates:
        session = LyricsSession(
            resources.conn,
            app,
            resources.index,
            resources.analyze,
            item["content_id"],
            "requested",
            store=resources.store,
            translate=translate.ask,
        )
        sessions[item["content_id"]] = session
        if pilot and item["content_id"] != 62:
            continue
        lines = session.translation()
        poem = Poem(
            item["content_id"],
            item["title"],
            session.units,
            [x.natural_en for x in lines],
            [x.literal_en for x in lines],
            [x.note_en for x in lines],
        )
        poems.append((poem, queue_poem(target, run_dir.name, poem)))
        log(f"  translated {poem.title} ({len(poem.lines)} lines)")

    judge_recorder = Recorder(
        _Asker(judge or ClaudeGenerator(model=JUDGE_MODEL, effort="medium", max_tokens=8000).ask)
    )
    judge_run, _ = judge_poems(target, poems, judge_recorder.ask, repeats, log=log)
    attempts = [a for a in ATTEMPTS if a.content_id in sessions]
    if pilot:
        attempts = [a for a in attempts if a.content_id == 56]
    attempt_sessions = {
        cid: s for cid, s in sessions.items() if any(a.content_id == cid for a in attempts)
    }
    attempt_run = judge_attempts(target, attempt_sessions, 2 if pilot else repeats, attempts)
    resources.conn.close()

    summary = {
        "poems": [p.title for p, _ in poems],
        "lines": sum(len(p.lines) for p, _ in poems),
        "judge_run": judge_run,
        "attempt_run": attempt_run,
        "cents": {
            "translation": round(sum(cents(g) for _, g in translate.calls), 2),
            "judge": round(sum(cents(g, JUDGE_MODEL) for _, g in judge_recorder.calls), 2),
            "attempts": round(sum(cents(g) for _, g in app.calls), 2),
        },
    }
    summary["cents"]["total"] = round(sum(summary["cents"].values()), 2)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


class _Asker:
    """Wraps a bare `ask` function so a Recorder can record it."""

    def __init__(self, ask: Callable[..., Generation]):
        self.ask = ask
