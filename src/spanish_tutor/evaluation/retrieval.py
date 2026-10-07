"""Retrieval quality (slice 4.4e): RAGAS's two retrieval metrics, implemented directly.

RAGAS can't be installed here (its dependencies need huggingface-hub >= 1.0; the pinned
transformers 4.53.2 needs < 1.0), so its definitions are implemented with Claude Sonnet
5.5 as the judge (medium effort, one judgment per item):

1. Context relevance of the conversation's example sentences. Each tutor reply gets up to
   4 Tatoeba sentences the learner can read (Tutor._examples: the topic plus the learner's
   message as the query, only fully known words). For the benchmark's 40 learner messages
   the same retrieval is re-run (local, free) against the frozen snapshot's vocabulary,
   and the judge labels each sentence relevant / partly / off for the message.
   (Approximation: the snapshot's vocabulary, without the words a conversation pre-taught
   or taught before that message.)

2. Faithfulness of the talk about a text. After reading, each tutor reply is grounded in
   the 3 passages retrieved for the learner's message (reading.TextPassages). Two scripted
   discussions (QUESTIONS: chapters 1-2 of An Elementary Spanish Reader, two of them traps
   whose answer the text doesn't give) run with the real tutor on a database copy. The
   judge lists the reply's claims about the story and checks each against the passages
   and the whole text: supported by the passages, in the text but not the passages (a
   retrieval gap), or not in the text (invented). Faithfulness, as RAGAS defines it,
   is supported / all claims. The judge also labels the passages' relevance to the
   question.

Items and judgments go in the real database (eval_items, ratings) under one judge run.

    python -m spanish_tutor.evaluation retrieval BENCHMARK_RUN_DIR
"""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from spanish_tutor import db
from spanish_tutor.conversation import (
    EXAMPLES_PER_TURN,
    ClaudeGenerator,
    Generation,
    Resources,
    load_resources,
)
from spanish_tutor.evaluation.benchmark import (
    BASE_DB,
    EVAL_DIR,
    Recorder,
    cents,
    copy_database,
    read_results,
)
from spanish_tutor.evaluation.metrics import Rate
from spanish_tutor.evaluation.translation import JUDGE_MODEL, Asker, queue_item, start_run
from spanish_tutor.reading import ReadingSession
from spanish_tutor.vectorstore import search_sentences

RUNS_DIR = EVAL_DIR / "retrieval"
PROMPT_VERSION = "retrieval-judge v1"
RELEVANCE = ("relevant", "partly", "off")

# The scripted questions (Jason reviewed them, 2026-10-07). The last of each is a trap:
# the text never gives the answer, so a faithful tutor says so.
QUESTIONS = {
    19: [  # El cuento del pollo
        "¿Por qué cree el pollo que el cielo ha caído?",
        "¿Qué animales van con el pollo al palacio?",
        "¿Quién les enseña el camino al palacio?",
        "¿Qué pasa al final con los animales?",
        "¿Cómo se llama el rey?",
    ],
    20: [  # Un hombre insaciable
        "¿Qué tenía el hombre rico?",
        "¿Por qué no estaba satisfecho el hombre?",
        "¿Qué le dice el extranjero en la viña?",
        "¿Cómo eran las uvas en el otoño, y por qué?",
        "¿Quién es el extranjero, Dios o el diablo?",
    ],
}
TRAPS = {(19, 5), (20, 5)}


# --- 1. Context relevance of the conversation's examples -------------------------------


class SentenceRelevance(BaseModel):
    number: int
    label: Literal["relevant", "partly", "off"] = Field(
        description="relevant: about the same thing as the learner's message or the topic, "
        "useful as an example of how to talk about it; partly: loosely related (shares a "
        "word or setting); off: unrelated."
    )


class RelevanceJudgment(BaseModel):
    sentences: list[SentenceRelevance]


def relevance_prompt(topic: str, message: str, sentences: list[str]) -> str:
    listing = "\n".join(f"{n}. {s}" for n, s in enumerate(sentences, 1))
    return (
        "A Spanish tutor app retrieves a few example sentences to guide its reply to a "
        "learner. For each sentence, judge whether it is relevant to what the learner is "
        "talking about: the conversation topic and, above all, the learner's last message. "
        "Label every number exactly once.\n\n"
        f"Topic: {topic}\nLearner's message: {message}\n\nRetrieved sentences:\n{listing}"
    )


def conversation_examples(resources: Resources, results: list[dict]) -> list[dict]:
    """Re-run the tutor's example retrieval for each benchmark message (local, free)."""
    known = db.known_vocabulary(resources.conn)
    found = []
    for r in results:
        query = f"{r['topic']} {r['text']}".strip()
        hits = search_sentences(resources.store, query, known, k=EXAMPLES_PER_TURN, max_unknown=0)
        if len(hits) < 2:
            hits = search_sentences(
                resources.store, query, known, k=EXAMPLES_PER_TURN, max_unknown=1
            )
        found.append({**r, "hits": [{"id": h.sentence_id, "es": h.es, "en": h.en} for h in hits]})
    return found


def judge_relevance(
    conn: sqlite3.Connection, run_id: int, run_name: str, examples: list[dict], ask
) -> list[Generation]:
    calls = []
    for n, r in enumerate(examples, 1):
        if not r["hits"]:
            continue
        sentences = [h["es"] for h in r["hits"]]
        generation = ask(RelevanceJudgment, relevance_prompt(r["topic"], r["text"], sentences))
        calls.append(generation)
        labels = {s.number: s.label for s in generation.reply.sentences}
        with conn:
            for k, hit in enumerate(r["hits"], 1):
                if k not in labels:
                    continue
                item = queue_item(
                    conn,
                    "retrieval",
                    f"retrieval:examples:{run_name}:{n}:tatoeba:{hit['id']}",
                    {
                        "kind": "conversation example",
                        "topic": r["topic"],
                        "message": r["text"],
                        "sentence": hit["es"],
                        "sentence_en": hit["en"],
                    },
                )
                conn.execute(
                    "INSERT INTO ratings (item_id, criterion, rater, run_id, label) "
                    "VALUES (?, 'relevance', ?, ?, ?)",
                    (item, JUDGE_MODEL, run_id, labels[k]),
                )
    return calls


# --- 2. Faithfulness of the talk about a text --------------------------------------------


class Claim(BaseModel):
    claim: str = Field(description="One statement the reply makes about the story, in English.")
    support: Literal["passages", "text_only", "not_in_text"] = Field(
        description="passages: the retrieved passages support it; text_only: the full text "
        "supports it but the passages don't; not_in_text: the text doesn't say it (invented "
        "or contradicted)."
    )


class FaithfulnessJudgment(BaseModel):
    claims: list[Claim] = Field(
        description="Every claim about the story's content, people or events. Not greetings, "
        "questions to the learner, language corrections, or opinions marked as opinions."
    )
    passages_relevance: Literal["relevant", "partly", "off"] = Field(
        description="Whether the retrieved passages hold what's needed to answer the "
        "learner's question (relevant), some of it (partly), or nothing (off)."
    )
    says_text_is_silent: bool = Field(
        description="True if the reply says or clearly implies that the story doesn't give "
        "the answer to the learner's question."
    )


def faithfulness_prompt(
    title: str, text: str, question: str, passages: list[str], reply: str
) -> str:
    listing = "\n".join(f"[{n}] {p}" for n, p in enumerate(passages, 1))
    return (
        "A Spanish tutor app talks with a learner about a story they just read. For each "
        "learner message the tutor is given some of the story (a few retrieved passages, or "
        "the whole of a short story), and it replies. "
        "Check the reply's faithfulness to the story: list each claim it makes about the "
        "story and say where its support is. Judge content, not language.\n\n"
        f"Story: «{title}»\n{text}\n\n"
        f"Learner's message: {question}\n\nWhat the tutor was given:\n{listing}\n\n"
        f"Tutor's reply: {reply}"
    )


def run_discussions(
    resources: Resources,
    questions: dict[int, list[str]] = QUESTIONS,
    log: Callable[[str], None] = print,
) -> list[dict]:
    """Read, finish and talk about each text with the real tutor (on a database copy),
    recording each reply with what the tutor was given: the passages retrieved for it,
    or, for a short text, the whole text (reading.WHOLE_TEXT_WORDS)."""
    found = []
    embeddings = resources.store.embeddings if resources.store is not None else None
    for content_id, asked in questions.items():
        session = ReadingSession(
            resources.conn,
            resources.generate,
            resources.index,
            resources.analyze,
            content_id,
            "requested",
            store=resources.store,
        )
        session.finish()
        talk = session.discuss(embeddings)
        talk.open()
        for number, question in enumerate(asked, 1):
            if talk.passages:
                passages, given = talk.passages.search(question), "passages"
            else:
                passages, given = [session.whole_text().strip()], "whole text"
            turn = talk.respond(question)
            found.append(
                {
                    "content_id": content_id,
                    "title": session.item["title"],
                    "number": number,
                    "question": question,
                    "passages": passages,
                    "given": given,
                    "reply": turn.reply_es,
                    "trap": (content_id, number) in TRAPS,
                }
            )
        log(f"  discussed {session.item['title']} ({len(asked)} questions)")
    return found


def judge_faithfulness(
    conn: sqlite3.Connection,
    run_id: int,
    run_name: str,
    discussions: list[dict],
    texts: dict[int, str],
    ask,
) -> list[Generation]:
    calls = []
    for d in discussions:
        generation = ask(
            FaithfulnessJudgment,
            faithfulness_prompt(
                d["title"], texts[d["content_id"]], d["question"], d["passages"], d["reply"]
            ),
        )
        calls.append(generation)
        judged = generation.reply
        supported = sum(1 for c in judged.claims if c.support == "passages")
        with conn:
            item = queue_item(
                conn,
                "reply",
                f"reading:{run_name}:{d['content_id']}:{d['number']}",
                {k: d[k] for k in ("title", "question", "passages", "reply", "trap")},
            )
            rows = [
                (
                    "faithfulness",
                    supported / len(judged.claims) if judged.claims else None,
                    None,
                    json.dumps([c.model_dump() for c in judged.claims], ensure_ascii=False),
                ),
                ("passages_relevance", None, judged.passages_relevance, None),
                ("says_text_is_silent", 1.0 if judged.says_text_is_silent else 0.0, None, None),
            ]
            for criterion, score, label, rationale in rows:
                if score is None and label is None:
                    continue
                conn.execute(
                    "INSERT INTO ratings (item_id, criterion, rater, run_id, score, label, rationale) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (item, criterion, JUDGE_MODEL, run_id, score, label, rationale),
                )
    return calls


# --- Summary -------------------------------------------------------------------------------


def summarize(conn: sqlite3.Connection, run_id: int) -> dict:
    """Context precision, faithfulness and the traps, from a retrieval judge run."""
    labels = [
        (r[0], r[1])
        for r in conn.execute(
            "SELECT criterion, label FROM ratings WHERE run_id = ? AND label IS NOT NULL", (run_id,)
        )
    ]
    examples = [label for criterion, label in labels if criterion == "relevance"]
    passages = [label for criterion, label in labels if criterion == "passages_relevance"]
    claims: list[dict] = []
    traps = []
    for content, rationale, criterion, score in conn.execute(
        "SELECT i.content, r.rationale, r.criterion, r.score FROM ratings AS r "
        "JOIN eval_items AS i USING (item_id) WHERE r.run_id = ? "
        "AND r.criterion IN ('faithfulness', 'says_text_is_silent')",
        (run_id,),
    ):
        item = json.loads(content)
        if criterion == "faithfulness" and rationale:
            claims += [{**c, "question": item["question"]} for c in json.loads(rationale)]
        if criterion == "says_text_is_silent" and item["trap"]:
            traps.append((item["question"], bool(score), item["reply"]))
    supported = sum(1 for c in claims if c["support"] == "passages")
    return {
        "examples_relevant": Rate(examples.count("relevant"), len(examples)),
        "examples_relevant_or_partly": Rate(
            examples.count("relevant") + examples.count("partly"), len(examples)
        ),
        "passages_relevant": Rate(passages.count("relevant"), len(passages)),
        "faithfulness": Rate(supported, len(claims)),
        "text_only": [c for c in claims if c["support"] == "text_only"],
        "invented": [c for c in claims if c["support"] == "not_in_text"],
        "traps": traps,
    }


def format_summary(found: dict) -> str:
    lines = [
        "Context relevance (conversation examples)",
        f"  relevant:            {found['examples_relevant']}",
        f"  relevant or partly:  {found['examples_relevant_or_partly']}",
        "Talk about a text",
        f"  passages hold the answer: {found['passages_relevant']}",
        f"  faithfulness (claims supported by the passages): {found['faithfulness']}",
    ]
    for c in found["text_only"]:
        lines.append(f"    in the text, not the passages: {c['claim']}  [{c['question']}]")
    for c in found["invented"]:
        lines.append(f"    NOT IN THE TEXT: {c['claim']}  [{c['question']}]")
    for question, silent, reply in found["traps"]:
        lines.append(
            f"  trap: {question} -> {'says the text is silent' if silent else 'ANSWERS'}: {reply}"
        )
    return "\n".join(lines)


# --- Running ---------------------------------------------------------------------------------


def run(
    benchmark_runs: list[Path],
    base: Path = BASE_DB,
    runs_dir: Path = RUNS_DIR,
    target: sqlite3.Connection | None = None,
    load: Callable[[Path], Resources] = lambda path: load_resources(db_path=path),
    judge: Callable[..., Generation] | None = None,
    log: Callable[[str], None] = print,
    examples: bool = True,
) -> dict:
    """Both measures, or (examples=False) only the talk about a text."""
    run_dir = runs_dir / datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
    copy = run_dir / "retrieval.db"
    copy_database(base, copy)
    resources = load(copy)
    tutor = Recorder(resources.generate)
    resources = replace(resources, generate=tutor)
    target = target or db.connect()
    db.init_schema(target)
    judge_recorder = Recorder(
        Asker(judge or ClaudeGenerator(model=JUDGE_MODEL, effort="medium", max_tokens=8000).ask)
    )
    run_id = start_run(
        target,
        JUDGE_MODEL,
        PROMPT_VERSION,
        {"benchmark_runs": [p.name for p in benchmark_runs], "retrieval_run": run_dir.name},
    )

    results = [r for p in benchmark_runs for r in read_results(p)]
    if examples:
        found = conversation_examples(resources, results)
        log(f"  retrieved examples for {len(found)} benchmark messages")
        judge_relevance(target, run_id, run_dir.name, found, judge_recorder.ask)

    discussions = run_discussions(resources, log=log)
    texts = {
        cid: resources.conn.execute(
            "SELECT text_es FROM content_items WHERE content_id = ?", (cid,)
        ).fetchone()[0]
        for cid in QUESTIONS
    }
    judge_faithfulness(target, run_id, run_dir.name, discussions, texts, judge_recorder.ask)
    resources.conn.close()

    (run_dir / "discussions.jsonl").write_text(
        "".join(json.dumps(d, ensure_ascii=False) + "\n" for d in discussions), encoding="utf-8"
    )
    summary = {
        "run_id": run_id,
        "cents": {
            "tutor": round(sum(cents(g) for _, g in tutor.calls), 2),
            "judge": round(sum(cents(g, JUDGE_MODEL) for _, g in judge_recorder.calls), 2),
        },
    }
    summary["cents"]["total"] = round(sum(summary["cents"].values()), 2)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary
