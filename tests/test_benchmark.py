"""The conversation benchmark: the script, the runner (with a scripted model), the scoring."""

import json
import sqlite3

import pytest
from fakes import Scripted, analyze, make_topic_store, said, seed_bank

from spanish_tutor import db
from spanish_tutor.conversation import Generation, Resources
from spanish_tutor.evaluation import benchmark
from spanish_tutor.evaluation.benchmark import (
    Message,
    PlantedError,
    Script,
    Topic,
    cents,
    load_script,
    queue_new_word_flags,
    score_grading,
)
from spanish_tutor.topics import TopicWords
from spanish_tutor.words import LexiconIndex

# --- The script -----------------------------------------------------------------------------


def test_the_script_has_40_messages_24_with_one_planted_mistake():
    script = load_script()
    messages = [m for t in script.topics for m in t.messages]
    errors = [m.error for m in messages if m.error]
    assert (len(script.topics), len(messages), len(errors)) == (8, 40, 24)
    assert sorted(e.kind for e in errors).count("wrong_word") == 10
    assert all(len(t.messages) == 5 for t in script.topics)


def test_every_planted_mistake_is_written_in_its_message():
    for topic in load_script().topics:
        for message in topic.messages:
            if message.error:
                assert message.error.written in message.text, message.text
                assert message.error.kind in ("wrong_form", "wrong_word")


# --- Scoring the grading --------------------------------------------------------------------


def result(text, flags=(), error=None):
    return {
        "text": text,
        "error": error,
        "flags": [{"written": w, "wrong_word": word} for w, word in flags],
    }


def planted(written, kind):
    return {"written": written, "intended": "?", "kind": kind, "type": "?"}


def test_grading_scores_detection_classification_and_false_alarms():
    results = [
        result("Mi jardín es muy bonita.", [("bonita", False)], planted("bonita", "wrong_form")),
        result(
            "Hoy soy muy ocupado.", [("soy", False)], planted("soy", "wrong_word")
        ),  # wrong kind
        result("A veces jugo.", [], planted("jugo", "wrong_word")),  # missed
        result("Tengo un jardín pequeño."),  # clean, no flags
        result("Me gusta regar.", [("regar", False)]),  # clean, a false alarm
    ]
    found = score_grading(results)
    assert (found["detected"].k, found["detected"].n) == (2, 3)
    assert (found["classified"].k, found["classified"].n) == (1, 2)
    assert (found["clean_without_flags"].k, found["clean_without_flags"].n) == (1, 2)
    assert [a["written"] for a in found["false_alarms"]] == ["regar"]
    assert [m["text"] for m in found["misses"]] == ["Hoy soy muy ocupado.", "A veces jugo."]
    word = found["by_kind"]["wrong_word"]
    assert (word["detected"].k, word["detected"].n, word["classified"].k) == (1, 2, 0)


def test_a_flag_on_part_of_a_phrase_or_twice_on_one_mistake_is_one_detection():
    results = [
        result(
            "Mi tío hoy es enfermo.",
            [("es enfermo", True), ("es", True)],
            planted("es enfermo", "wrong_word"),
        ),
        result(
            "Mis padres está contentos.", [("Padres está", False)], planted("está", "wrong_form")
        ),
    ]
    found = score_grading(results)
    assert (found["detected"].k, found["classified"].k) == (2, 2)
    assert found["false_alarms"] == []  # a second flag on the same mistake isn't an alarm


def test_a_flag_on_another_word_beside_a_mistake_is_a_false_alarm():
    results = [
        result(
            "Ayer trabajo mucho.",
            [("trabajo", False), ("mucho", False)],
            planted("trabajo", "wrong_form"),
        )
    ]
    assert [a["written"] for a in score_grading(results)["false_alarms"]] == ["mucho"]


# --- Cost -----------------------------------------------------------------------------------


def test_cents_prices_each_kind_of_token():
    # 1M plain input $4, 1M 5-minute writes $5, 1M 1-hour writes $8, 1M reads $0.20,
    # 1M output $20: the input count includes the writes.
    g = Generation(
        None,
        input_tokens=3_000_000,
        cache_read_tokens=1_000_000,
        cache_write_5m_tokens=1_000_000,
        cache_write_1h_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert cents(g) == pytest.approx((4 + 5 + 8 + 0.2 + 20) * 100)


# --- Running (a scripted model) -------------------------------------------------------------


@pytest.fixture
def base(tmp_path):
    path = tmp_path / "base.db"
    conn = db.connect(path)
    db.init_schema(conn)
    seed_bank(conn)
    # The real history the snapshot carries: a conversation whose reply taught "río".
    real = db.start_session(conn, "conversation", "m", "el agua")
    reply = db.add_turn(conn, real, 1, "tutor", "El río.", draft_out_of_bank=1, final_out_of_bank=1)
    conn.execute(
        "INSERT INTO word_events (lexeme_id, mode, event_type, source, turn_id) "
        "SELECT lexeme_id, 'recognition', 'taught', 'conversation', ? FROM lexemes "
        "WHERE lemma = 'río'",
        (reply,),
    )
    conn.commit()
    conn.close()
    return path


SCRIPT = Script(
    version=1,
    pre_teach=2,
    topics=[
        Topic(
            "los animales",
            [
                Message(
                    "Yo soy cansado hoy.", PlantedError("soy", "estoy", "wrong_word", "ser/estar")
                ),
                Message("Yo estoy cansado hoy."),
            ],
        )
    ],
)


def test_a_run_records_each_message_with_its_flags_on_a_copy(base, tmp_path, make_wiktionary):
    generator = Scripted(
        TopicWords(words=["perro|NOUN"], practice=[], fewer_because=None),
        "Hola.",
        said("Bien.", wrong_words=["soy"]),
        "¡Bien!",
    )
    store = None

    def load(path):
        nonlocal store
        conn = db.connect(path)
        store = make_topic_store(conn, tmp_path / "chroma")
        index = LexiconIndex(conn, make_wiktionary([]))
        return Resources(conn, generator, index, analyze, store)

    run_dir = benchmark.run(base=base, runs_dir=tmp_path / "runs", load=load, script=SCRIPT)

    rows = benchmark.read_results(run_dir)
    assert [(r["message_no"], r["text"]) for r in rows] == [
        (1, "Yo soy cansado hoy."),
        (2, "Yo estoy cansado hoy."),
    ]
    assert rows[0]["flags"] == [{"written": "soy", "wrong_word": True}]
    assert rows[1]["flags"] == [] and rows[1]["used"] == ["yo", "estar", "cansado", "hoy"]
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["calls"] == 4 and summary["cents"] > 0
    assert set(summary["cents_by_kind"]) == {"reply", "select"}

    copy = sqlite3.connect(run_dir / "benchmark.db")
    assert copy.execute("SELECT kind, prompt_version FROM eval_runs").fetchone() == (
        "benchmark",
        "benchmark.json v1",
    )
    assert copy.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 6  # 1 real + 5
    copy.close()
    original = sqlite3.connect(base)  # the snapshot is untouched
    assert original.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 1
    original.close()

    found = benchmark.score(run_dir)
    assert (found["grading"]["detected"].k, found["grading"]["classified"].k) == (1, 1)
    # Only the run's own replies: not the real conversation the snapshot also holds.
    assert summary["first_session"] == 2
    assert found["metrics"]["adherence"]["all"]["replies"] == 3
    target = db.connect(":memory:")
    db.init_schema(target)
    copy = sqlite3.connect(run_dir / "benchmark.db")
    queue_new_word_flags(copy, target, "benchmark:r:", from_session=summary["first_session"])
    queued = [json.loads(c)["lemma"] for (c,) in target.execute("SELECT content FROM eval_items")]
    assert "río" not in queued
    copy.close()


def test_freezing_the_snapshot_happens_once(base, tmp_path):
    target = tmp_path / "frozen.db"
    benchmark.freeze_base(base, target)
    conn = db.connect(base)
    conn.execute("INSERT INTO lexemes (lemma, pos) VALUES ('nuevo', 'ADJ')")
    conn.commit()
    conn.close()
    benchmark.freeze_base(base, target)  # already there: kept as it was
    frozen = sqlite3.connect(target)
    assert frozen.execute("SELECT COUNT(*) FROM lexemes WHERE lemma = 'nuevo'").fetchone()[0] == 0
    frozen.close()


# --- The rating queue -----------------------------------------------------------------------


def test_words_the_replies_taught_are_queued_for_rating_once(conn):
    source = db.connect(":memory:")
    db.init_schema(source)
    seed_bank(source)
    session = db.start_session(source, "conversation", "m", "los animales")
    opening = db.add_turn(source, session, 1, "tutor", "Hola, el perro.", final_out_of_bank=1)
    perro = source.execute("SELECT lexeme_id FROM lexemes WHERE lemma = 'perro'").fetchone()[0]
    nadar = source.execute("SELECT lexeme_id FROM lexemes WHERE lemma = 'nadar'").fetchone()[0]
    source.executemany(
        "INSERT INTO word_events (lexeme_id, mode, event_type, source, turn_id) "
        "VALUES (?, 'recognition', 'taught', ?, ?)",
        [(perro, "conversation", opening), (nadar, "pre_teach", opening)],  # pre-taught: not a flag
    )

    assert queue_new_word_flags(source, conn, "benchmark:r1:") == 1
    assert queue_new_word_flags(source, conn, "benchmark:r1:") == 0  # already queued
    ref, content = conn.execute("SELECT source_ref, content FROM eval_items").fetchone()
    assert ref == f"benchmark:r1:turn:{opening}:lexeme:{perro}"
    assert json.loads(content) == {
        "lemma": "perro",
        "pos": "NOUN",
        "definition_en": "<perro>",
        "sentence": "Hola, el perro.",
        "topic": "los animales",
    }
