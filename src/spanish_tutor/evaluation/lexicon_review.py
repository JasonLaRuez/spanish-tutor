"""The LLM review of the word database (lexemes): is each entry right for a learner?

    uv run python -m spanish_tutor.evaluation lexicon-review ...   (see __main__.py)

A reviewer model (Claude Sonnet 5.5 through the Batch API, Jason's choice) sees 25 entries
per request, each with its lemma, part of speech, English definition and example sentence
with its translation, and reports only problems: the field, a verdict ("incorrect" or
"unsure"), a suggested fix and a reason. Every other reviewed field is recorded "correct",
so each field reviewed has a verdict. Verdicts go in lexeme_reviews (append-only, one
eval_runs row per run, migration 12); nothing in lexemes changes here.

Then, as for the translation judge, the reviewer is checked before it's trusted: two runs
on a pilot compared (consistency), errors planted on a database copy (recall), Opus 5.5 as
a second opinion on what was flagged, and Jason rating a sample of flags and of entries it
passed on the Rate page. Only fixes Jason accepts there are applied (`apply`): definitions
and example translations, with their provenance marked. A lemma, part of speech or Spanish
example is never changed automatically: a lemma or part of speech is the word's identity,
which learning history points to, and an example sentence carries its Tatoeba author.
"""

from __future__ import annotations

import json
import random
import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from spanish_tutor import progress, recommend
from spanish_tutor.config import DATA_DIR

REVIEWER = "claude-sonnet-5-5"
SECOND_OPINION = "claude-opus-5-5"
PROMPT_VERSION = "lexicon-review v1"
PER_REQUEST = 25
FIELDS = ("lemma", "pos", "definition_en", "example_es", "example_en")
APPLIED = ("definition_en", "example_en")  # the fields `apply` may change
STATE_DIR = DATA_DIR / "processed" / "eval" / "lexicon_review"
# Batch API prices ($ per million tokens: half the list price).
BATCH_PRICES = {REVIEWER: (1.0, 5.0), SECOND_OPINION: (2.0, 10.0)}
LIST_PRICES = {REVIEWER: (2.0, 10.0), SECOND_OPINION: (4.0, 20.0)}

PROBLEMS_SCHEMA = {
    "type": "object",
    "properties": {
        "problems": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "field": {"type": "string", "enum": list(FIELDS)},
                    "verdict": {"type": "string", "enum": ["incorrect", "unsure"]},
                    "suggestion": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "field", "verdict", "suggestion", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["problems"],
    "additionalProperties": False,
}

INSTRUCTIONS = """\
You are checking entries in a Spanish learner's dictionary. Each entry is a word (lemma), its
part of speech, a short English definition, and usually a Spanish example sentence with its
English translation. A learner will be taught the word from this entry.

Report only real problems, as a list. For each: the entry's id, the field (lemma, pos,
definition_en, example_es, example_en), the verdict ("incorrect" if it's wrong, "unsure" if
you doubt it), your suggested fix (what the field should say; empty if you can't tell) and a
one-sentence reason. Report nothing for an entry with no problem.

What counts as a problem:
- lemma: not a real Spanish word or expression, or misspelled (wrong or missing accent).
- pos: not a part of speech this word has (NOUN, VERB, ADJ, ADV, PRON, DET, ADP, CCONJ,
  SCONJ, INTJ, NUM, PART, EXPR for a multi-word expression).
- definition_en: wrong, or gives a rare, archaic, regional or slang sense where a common one
  exists, or doesn't fit the part of speech.
- example_es: doesn't use this word in the sense defined, or isn't natural Spanish.
- example_en: not an accurate translation of example_es.

Not problems: a short gloss ("house, home") rather than a full definition, British or
American spelling, a definition marked as model-written, punctuation or capitalization style.
"""


@dataclass(frozen=True)
class Entry:
    lexeme_id: int
    lemma: str
    pos: str
    definition_en: str | None
    definition_source: str | None
    example_es: str | None
    example_en: str | None

    def fields(self) -> tuple[str, ...]:
        """The fields this entry has to review (an entry without an example has 3)."""
        return FIELDS if self.example_es else FIELDS[:3]

    def shown(self) -> str:
        line = f"[{self.lexeme_id}] {self.lemma} ({self.pos}): {self.definition_en or '(none)'}"
        if (self.definition_source or "").startswith("model:"):
            line += "  [model-written definition]"
        if self.example_es:
            line += (
                f'\n    example: «{self.example_es}» = "{self.example_en or "(no translation)"}"'
            )
        return line


# --- Selection ----------------------------------------------------------------------------


def tier_a(conn: sqlite3.Connection, ceiling: float = recommend.MAX_UNKNOWN_SHARE) -> list[int]:
    """The words the learner meets soonest (sql/queries/review_tier_a.sql), most frequent first."""
    return [r["lexeme_id"] for r in progress.rows(conn, "review_tier_a", ceiling=ceiling)]


def entries(conn: sqlite3.Connection, ids: Sequence[int]) -> list[Entry]:
    found = {}
    for chunk in range(0, len(ids), 500):
        part = list(ids[chunk : chunk + 500])
        marks = ",".join("?" * len(part))
        for row in conn.execute(
            "SELECT lexeme_id, lemma, pos, definition_en, definition_source, example_es, "
            f"example_en FROM lexemes WHERE lexeme_id IN ({marks})",
            part,
        ):
            found[row[0]] = Entry(*row)
    return [found[i] for i in ids if i in found]


# --- Requests and answers -----------------------------------------------------------------


def prompt(batch: Sequence[Entry]) -> str:
    return INSTRUCTIONS + "\nEntries:\n" + "\n".join(e.shown() for e in batch)


def chunks(items: Sequence[Entry], size: int = PER_REQUEST) -> list[list[Entry]]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def verdict_rows(batch: Sequence[Entry], problems: Sequence[dict]) -> list[tuple]:
    """One (lexeme_id, field, verdict, suggestion, reason) per field of each entry: the
    reported problem, else 'correct'. A problem about an id not in the batch, or a field
    the entry doesn't have, is ignored; the first problem for a field wins."""
    by_key: dict[tuple[int, str], dict] = {}
    ids = {e.lexeme_id for e in batch}
    for p in problems:
        if p.get("id") in ids and p.get("field") in FIELDS:
            by_key.setdefault((p["id"], p["field"]), p)
    rows = []
    for e in batch:
        for field in e.fields():
            p = by_key.get((e.lexeme_id, field))
            if p is None:
                rows.append((e.lexeme_id, field, "correct", None, None))
            else:
                suggestion = (p.get("suggestion") or "").strip() or None
                rows.append((e.lexeme_id, field, p["verdict"], suggestion, p.get("reason")))
    return rows


def new_run(conn: sqlite3.Connection, model: str, config: dict, note: str | None = None) -> int:
    with conn:
        return conn.execute(
            "INSERT INTO eval_runs (kind, model, prompt_version, config, note) "
            "VALUES ('judge', ?, ?, ?, ?)",
            (model, PROMPT_VERSION, json.dumps(config), note),
        ).lastrowid


def store(conn: sqlite3.Connection, run_id: int, model: str, rows: Sequence[tuple]) -> None:
    with conn:
        conn.executemany(
            "INSERT INTO lexeme_reviews (lexeme_id, field, verdict, suggestion, reason, "
            "reviewer, run_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(*row, model, run_id) for row in rows],
        )


def request_params(batch: Sequence[Entry], model: str) -> dict:
    return {
        "model": model,
        "max_tokens": 8000,
        "messages": [{"role": "user", "content": prompt(batch)}],
        "output_config": {
            "effort": "low",
            "format": {"type": "json_schema", "schema": PROBLEMS_SCHEMA},
        },
    }


# --- The Batch API ------------------------------------------------------------------------


def submit(
    conn: sqlite3.Connection,
    items: Sequence[Entry],
    *,
    name: str,
    model: str = REVIEWER,
    note: str | None = None,
    client=None,
) -> dict:
    """Start a review run: one eval_runs row, one batch request per 25 entries. The state
    (run, batch id, each request's ids) is saved under STATE_DIR/<name>.json for `collect`."""
    import anthropic

    client = client or anthropic.Anthropic()
    batches = chunks(items)
    run_id = new_run(conn, model, {"entries": len(items), "per_request": PER_REQUEST}, note)
    batch = client.messages.batches.create(
        requests=[
            {"custom_id": f"lex-{i}", "params": request_params(b, model)}
            for i, b in enumerate(batches)
        ]
    )
    state = {
        "run_id": run_id,
        "model": model,
        "batch_id": batch.id,
        "chunks": [[e.lexeme_id for e in b] for b in batches],
    }
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{name}.json").write_text(json.dumps(state), encoding="utf-8")
    return state


def collect(conn: sqlite3.Connection, name: str, *, wait: bool = True, client=None) -> dict | None:
    """Store a submitted run's verdicts once its batch has ended; None if it's still running
    (and wait is False). A request that failed or didn't parse stores nothing, so its words
    stay unreviewed (and are reported)."""
    import anthropic

    client = client or anthropic.Anthropic()
    state = json.loads((STATE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    while True:
        batch = client.messages.batches.retrieve(state["batch_id"])
        if batch.processing_status == "ended":
            break
        if not wait:
            return None
        print(f"  {batch.request_counts.processing} requests still processing ...", flush=True)
        time.sleep(30)
    rows, tokens_in, tokens_out, failed = [], 0, 0, []
    for result in client.messages.batches.results(state["batch_id"]):
        index = int(result.custom_id.split("-", 1)[1])
        batch_entries = entries(conn, state["chunks"][index])
        if result.result.type != "succeeded":
            failed.append(index)
            continue
        message = result.result.message
        tokens_in += message.usage.input_tokens
        tokens_out += message.usage.output_tokens
        try:
            text = next(b.text for b in message.content if b.type == "text")
            problems = json.loads(text)["problems"]
        except (StopIteration, ValueError, KeyError):
            failed.append(index)
            continue
        rows += verdict_rows(batch_entries, problems)
    store(conn, state["run_id"], state["model"], rows)
    price_in, price_out = BATCH_PRICES[state["model"]]
    summary = {
        "run_id": state["run_id"],
        "rows": len(rows),
        "flagged": sum(r[2] != "correct" for r in rows),
        "failed_requests": failed,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "dollars": (tokens_in * price_in + tokens_out * price_out) / 1e6,
    }
    state["collected"] = summary
    (STATE_DIR / f"{name}.json").write_text(json.dumps(state), encoding="utf-8")
    return summary


# --- Second opinion -------------------------------------------------------------------------


def flagged(conn: sqlite3.Connection, run_id: int) -> list[int]:
    """The words a run flagged (any field incorrect or unsure)."""
    return [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT lexeme_id FROM lexeme_reviews WHERE run_id = ? AND verdict <> 'correct' "
            "ORDER BY lexeme_id",
            (run_id,),
        )
    ]


def second_opinion(conn: sqlite3.Connection, run_id: int, ask, model: str = SECOND_OPINION) -> dict:
    """Opus reviews only what a run flagged, the same way (synchronous calls through `ask`:
    prompt -> (problems, tokens in, tokens out)), as a run of its own."""
    ids = flagged(conn, run_id)
    second = new_run(conn, model, {"second_opinion_of": run_id, "entries": len(ids)})
    rows, tokens_in, tokens_out = [], 0, 0
    for batch in chunks(entries(conn, ids)):
        problems, t_in, t_out = ask(prompt(batch))
        rows += verdict_rows(batch, problems)
        tokens_in, tokens_out = tokens_in + t_in, tokens_out + t_out
    store(conn, second, model, rows)
    price_in, price_out = LIST_PRICES[model]
    return {
        "run_id": second,
        "entries": len(ids),
        "flagged": sum(r[2] != "correct" for r in rows),
        "dollars": (tokens_in * price_in + tokens_out * price_out) / 1e6,
    }


def claude_ask(model: str = SECOND_OPINION):
    """A synchronous `ask` for second_opinion, with the same structured output."""
    import anthropic

    client = anthropic.Anthropic()

    def ask(text: str) -> tuple[list[dict], int, int]:
        params = request_params([], model) | {"messages": [{"role": "user", "content": text}]}
        message = client.messages.create(**params)
        body = next(b.text for b in message.content if b.type == "text")
        return json.loads(body)["problems"], message.usage.input_tokens, message.usage.output_tokens

    return ask


# --- Measuring the reviewer -----------------------------------------------------------------


def verdicts(conn: sqlite3.Connection, run_id: int) -> dict[tuple[int, str], str]:
    return {
        (r[0], r[1]): r[2]
        for r in conn.execute(
            "SELECT lexeme_id, field, verdict FROM lexeme_reviews WHERE run_id = ?", (run_id,)
        )
    }


def consistency(conn: sqlite3.Connection, run_a: int, run_b: int) -> dict:
    """How two runs over the same words agree, per field: flagged (incorrect or unsure) or
    not. Krippendorff's alpha (nominal) over the fields both runs judged, and the counts."""
    from spanish_tutor.evaluation.agreement import alpha

    a, b = verdicts(conn, run_a), verdicts(conn, run_b)
    shared = sorted(a.keys() & b.keys())
    pairs = [(a[k] != "correct", b[k] != "correct") for k in shared]
    return {
        "fields": len(shared),
        "flagged_a": sum(x for x, _ in pairs),
        "flagged_b": sum(y for _, y in pairs),
        "both": sum(x and y for x, y in pairs),
        "alpha": alpha([[int(x), int(y)] for x, y in pairs], metric="nominal") if pairs else None,
    }


def recall(conn: sqlite3.Connection, run_id: int, planted: Sequence[tuple[int, str]]) -> dict:
    """How many planted errors (lexeme_id, field) a run flagged."""
    v = verdicts(conn, run_id)
    caught = [p for p in planted if v.get(tuple(p), "correct") != "correct"]
    return {
        "planted": len(planted),
        "caught": len(caught),
        "missed": [p for p in planted if p not in caught],
    }


# --- Rating and applying --------------------------------------------------------------------


def queue_for_rating(
    conn: sqlite3.Connection,
    run_id: int,
    second_run: int | None,
    *,
    entries_sample: int = 40,
    seed: int = 7,
) -> tuple[int, int]:
    """Queue the run's flags (with the second opinion, if any) and a random sample of the
    words it passed, for Jason on the Rate page. Returns (flags, entries) queued."""
    from spanish_tutor.evaluation.translation import queue_item

    second = verdicts(conn, second_run) if second_run else {}
    rows = conn.execute(
        """
        SELECT r.lexeme_id, r.field, r.verdict, r.suggestion, r.reason, l.lemma, l.pos,
               l.definition_en, l.example_es, l.example_en
        FROM lexeme_reviews AS r JOIN lexemes AS l USING (lexeme_id)
        WHERE r.run_id = ? AND r.verdict <> 'correct'
        ORDER BY r.lexeme_id, r.field
        """,
        (run_id,),
    ).fetchall()
    with conn:
        for r in rows:
            current = {"lemma": r[5], "pos": r[6], "definition_en": r[7], "example_es": r[8],
                       "example_en": r[9]}  # fmt: skip
            queue_item(
                conn,
                "lexeme_flag",
                f"lexeme_reviews:{run_id}:{r[0]}:{r[1]}",
                {
                    "lexeme_id": r[0], "lemma": r[5], "pos": r[6], "field": r[1],
                    "current": current[r[1]], "verdict": r[2], "suggestion": r[3],
                    "reason": r[4], "definition_en": r[7], "example_es": r[8], "example_en": r[9],
                    "second_opinion": second.get((r[0], r[1])),
                },
            )  # fmt: skip
        passed = [
            row[0]
            for row in conn.execute(
                "SELECT lexeme_id FROM lexeme_reviews WHERE run_id = ? GROUP BY lexeme_id "
                "HAVING SUM(verdict <> 'correct') = 0 ORDER BY lexeme_id",
                (run_id,),
            )
        ]
        sample = random.Random(seed).sample(passed, min(entries_sample, len(passed)))
        for e in entries(conn, sample):
            queue_item(
                conn,
                "lexeme_entry",
                f"lexeme_reviews:{run_id}:{e.lexeme_id}",
                {"lexeme_id": e.lexeme_id, "lemma": e.lemma, "pos": e.pos,
                 "definition_en": e.definition_en, "example_es": e.example_es,
                 "example_en": e.example_en},
            )  # fmt: skip
    return len(rows), len(sample)


GROUPS = ("both", "primary_only", "secondary_only", "unsure")


def flag_groups(
    conn: sqlite3.Connection, primary: int, secondary: int
) -> dict[str, list[tuple[int, str]]]:
    """Each flagged (lexeme_id, field), by how sure the two reviewers are: both say
    incorrect; only the primary (Opus) does; only the secondary (Sonnet) does; or neither
    says incorrect but one is unsure."""
    a, b = verdicts(conn, primary), verdicts(conn, secondary)
    groups: dict[str, list[tuple[int, str]]] = {g: [] for g in GROUPS}
    for key in sorted(a.keys() | b.keys()):
        x, y = a.get(key, "correct"), b.get(key, "correct")
        if x == "incorrect" and y == "incorrect":
            groups["both"].append(key)
        elif x == "incorrect":
            groups["primary_only"].append(key)
        elif y == "incorrect":
            groups["secondary_only"].append(key)
        elif "unsure" in (x, y):
            groups["unsure"].append(key)
    return groups


def opinions(conn: sqlite3.Connection, run_id: int) -> dict[tuple[int, str], tuple]:
    return {
        (r[0], r[1]): (r[2], r[3], r[4])
        for r in conn.execute(
            "SELECT lexeme_id, field, verdict, suggestion, reason FROM lexeme_reviews WHERE run_id = ?",
            (run_id,),
        )
    }


def queue_groups(
    conn: sqlite3.Connection,
    primary: int,
    secondary: int,
    *,
    per_group: int = 25,
    passed_sample: int = 40,
    seed: int = 7,
) -> dict[str, int]:
    """Queue a random sample of each group's flags (both reviewers' opinions shown; the
    suggestion applied on "fix" is the primary's, else the secondary's), and a sample of
    the words both passed, for Jason on the Rate page. Returns how many were queued."""
    from spanish_tutor.evaluation.translation import queue_item

    rng = random.Random(seed)
    groups = flag_groups(conn, primary, secondary)
    first, second = opinions(conn, primary), opinions(conn, secondary)
    queued = {}
    with conn:
        for group, keys in groups.items():
            chosen = rng.sample(keys, min(per_group, len(keys)))
            for lexeme_id, field in sorted(chosen):
                (e,) = entries(conn, [lexeme_id])
                p, s = first.get((lexeme_id, field)), second.get((lexeme_id, field))
                main, other, other_name = (
                    (p, s, "Sonnet") if p and p[0] != "correct" else (s, p, "Opus")
                )
                other_text = (
                    f"{other_name}: {other[0]}" + (f" — “{other[1]}”" if other[1] else "")
                    + (f" — {other[2]}" if other[2] else "")
                    if other else None
                )  # fmt: skip
                queue_item(
                    conn,
                    "lexeme_flag",
                    f"lexeme_reviews:{primary}+{secondary}:{lexeme_id}:{field}",
                    {
                        "lexeme_id": lexeme_id, "lemma": e.lemma, "pos": e.pos, "field": field,
                        "current": getattr(e, field), "group": group,
                        "verdict": main[0], "suggestion": main[1], "reason": main[2],
                        "second_opinion": other_text,
                        "definition_en": e.definition_en, "example_es": e.example_es,
                        "example_en": e.example_en,
                    },
                )  # fmt: skip
            queued[group] = len(chosen)
        flagged_words = {k[0] for keys in groups.values() for k in keys}
        reviewed = {k[0] for k in verdicts(conn, primary)} & {
            k[0] for k in verdicts(conn, secondary)
        }
        passed = sorted(reviewed - flagged_words)
        sample = rng.sample(passed, min(passed_sample, len(passed)))
        for e in entries(conn, sample):
            queue_item(
                conn,
                "lexeme_entry",
                f"lexeme_reviews:{primary}+{secondary}:{e.lexeme_id}",
                {"lexeme_id": e.lexeme_id, "lemma": e.lemma, "pos": e.pos,
                 "definition_en": e.definition_en, "example_es": e.example_es,
                 "example_en": e.example_en},
            )  # fmt: skip
        queued["passed"] = len(sample)
    return queued


def group_precision(conn: sqlite3.Connection) -> dict[str, dict]:
    """From Jason's latest ratings: per group, how many flags were a real problem (fix or
    real_not_fix), how many fixes he accepted, and the passed words he found wrong."""
    from spanish_tutor.evaluation.metrics import Rate

    latest = conn.execute(
        """
        WITH latest AS (
            SELECT item_id, label,
                   ROW_NUMBER() OVER (PARTITION BY item_id ORDER BY rating_id DESC) AS n
            FROM ratings WHERE rater = 'human'
        )
        SELECT i.item_type, i.content, r.label FROM eval_items AS i JOIN latest AS r USING (item_id)
        WHERE r.n = 1 AND i.item_type IN ('lexeme_flag', 'lexeme_entry')
        """
    ).fetchall()
    out: dict[str, dict] = {}
    for group in GROUPS:
        labels = [
            lab
            for t, c, lab in latest
            if t == "lexeme_flag" and json.loads(c).get("group") == group
        ]
        out[group] = {
            "real": Rate(sum(lab != "not_a_problem" for lab in labels), len(labels)),
            "fix_accepted": Rate(sum(lab == "fix" for lab in labels), len(labels)),
        }
    entries_rated = [lab for t, _, lab in latest if t == "lexeme_entry"]
    out["passed"] = {
        "wrong": Rate(sum(lab == "wrong" for lab in entries_rated), len(entries_rated))
    }
    return out


def accepted_fixes(conn: sqlite3.Connection) -> list[dict]:
    """Flags Jason rated 'fix' (his latest rating counts), for the fields `apply` may change."""
    rows = conn.execute(
        """
        WITH latest AS (
            SELECT item_id, label,
                   ROW_NUMBER() OVER (PARTITION BY item_id ORDER BY rating_id DESC) AS n
            FROM ratings WHERE rater = 'human'
        )
        SELECT i.content FROM eval_items AS i JOIN latest AS r USING (item_id)
        WHERE i.item_type = 'lexeme_flag' AND r.n = 1 AND r.label = 'fix'
        """
    ).fetchall()
    fixes = [json.loads(r[0]) for r in rows]
    return [f for f in fixes if f["field"] in APPLIED and (f.get("suggestion") or "").strip()]


def apply(conn: sqlite3.Connection, fixes: Sequence[dict]) -> int:
    """Write accepted fixes to lexemes (one transaction): a definition gets '+reviewed' on its
    source; an example translation records the reviewer as its translator. Returns how many
    changed. A fix whose field changed since it was queued is skipped, not overwritten."""
    changed = 0
    with conn:
        for f in fixes:
            column = f["field"]
            current = conn.execute(
                f"SELECT {column} FROM lexemes WHERE lexeme_id = ?", (f["lexeme_id"],)
            ).fetchone()
            if current is None or current[0] != f["current"]:
                continue
            if column == "definition_en":
                conn.execute(
                    "UPDATE lexemes SET definition_en = ?, definition_source = "
                    "COALESCE(definition_source, 'unknown') || '+reviewed' WHERE lexeme_id = ?",
                    (f["suggestion"].strip(), f["lexeme_id"]),
                )
            else:
                conn.execute(
                    "UPDATE lexemes SET example_en = ?, example_en_source = ? WHERE lexeme_id = ?",
                    (f["suggestion"].strip(), f"reviewed:{REVIEWER}", f["lexeme_id"]),
                )
            changed += 1
    return changed


def report(conn: sqlite3.Connection, run_id: int) -> dict:
    """What a run found: verdicts per field."""
    out: dict[str, dict[str, int]] = {}
    for field, verdict, n in conn.execute(
        "SELECT field, verdict, COUNT(*) FROM lexeme_reviews WHERE run_id = ? GROUP BY 1, 2",
        (run_id,),
    ):
        out.setdefault(field, {})[verdict] = n
    return out


def plant_errors(
    conn: sqlite3.Connection, ids: Sequence[int], n: int = 20, seed: int = 11
) -> list[tuple[int, str]]:
    """On a DATABASE COPY: plant n errors among these words, to measure what the reviewer
    catches: definitions and example translations swapped with other words', parts of
    speech changed, lemmas misspelled. Returns (lexeme_id, field) for each planted error."""
    rng = random.Random(seed)
    pool = [e for e in entries(conn, ids) if e.example_es and e.definition_en and e.pos != "EXPR"]
    chosen = rng.sample(pool, n)
    planted = []
    with conn:
        for i, e in enumerate(chosen):
            other = pool[(pool.index(e) + 37) % len(pool)]
            kind = ("definition_en", "example_en", "pos", "lemma")[i % 4]
            if kind == "definition_en":
                conn.execute(
                    "UPDATE lexemes SET definition_en = ? WHERE lexeme_id = ?",
                    (other.definition_en, e.lexeme_id),
                )
            elif kind == "example_en":
                conn.execute(
                    "UPDATE lexemes SET example_en = ? WHERE lexeme_id = ?",
                    (other.example_en, e.lexeme_id),
                )
            elif kind == "pos":
                wrong = "VERB" if e.pos != "VERB" else "NOUN"
                if conn.execute(
                    "SELECT 1 FROM lexemes WHERE lemma = ? AND pos = ?", (e.lemma, wrong)
                ).fetchone():
                    continue
                conn.execute("UPDATE lexemes SET pos = ? WHERE lexeme_id = ?", (wrong, e.lexeme_id))
            else:
                if len(e.lemma) < 4:
                    continue
                j = len(e.lemma) // 2
                typo = e.lemma[: j - 1] + e.lemma[j] + e.lemma[j - 1] + e.lemma[j + 1 :]
                if (
                    typo == e.lemma
                    or conn.execute(
                        "SELECT 1 FROM lexemes WHERE lemma = ? AND pos = ?", (typo, e.pos)
                    ).fetchone()
                ):
                    continue
                conn.execute(
                    "UPDATE lexemes SET lemma = ? WHERE lexeme_id = ?", (typo, e.lexeme_id)
                )
            planted.append((e.lexeme_id, kind))
    return planted


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))
