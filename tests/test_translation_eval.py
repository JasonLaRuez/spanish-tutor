"""Translation naturalness (slice 4.4d): judging lines repeatedly, attempts, consistency."""

from dataclasses import dataclass

import pytest

from spanish_tutor.conversation import Generation
from spanish_tutor.evaluation import translation
from spanish_tutor.evaluation.translation import (
    ATTEMPTS,
    RUBRIC,
    Attempt,
    JudgedLine,
    Poem,
    TranslationJudgment,
    consistency,
    judge_attempts,
    judge_poems,
    judge_prompt,
    queue_poem,
    valid_judgment,
)

POEM = Poem(
    62,
    "Rima XXIII",
    ["Por una mirada, un mundo;", "Por una sonrisa, un cielo;"],
    ["For one glance, a world;", "For one smile, a heaven;"],
    ["For a look, a world;", "For a smile, a sky;"],
    [None, "Cielo is both sky and heaven."],
)


def judgment(*scores):
    """A judgment: (naturalness, faithfulness) per line, numbered from 1."""
    return TranslationJudgment(
        lines=[
            JudgedLine(line_no=n, naturalness=a, faithfulness=b, reason="r")
            for n, (a, b) in enumerate(scores, 1)
        ]
    )


def scripted(*replies):
    """An `ask` returning the replies in order, each with token counts."""
    queue = list(replies)
    asked = []

    def ask(schema, prompt):
        asked.append(prompt)
        return Generation(queue.pop(0), input_tokens=900, output_tokens=300)

    ask.asked = asked
    return ask


# --- The prompt and validation -----------------------------------------------------------


def test_the_prompt_has_the_rubric_and_every_line_with_its_english():
    prompt = judge_prompt(POEM)
    assert prompt.startswith(RUBRIC)
    assert "«Rima XXIII»" in prompt
    assert "1. Spanish: Por una mirada, un mundo;\n   English: For one glance, a world;" in prompt
    assert "For a look" not in prompt  # the literal translation isn't judged


@pytest.mark.parametrize(
    "reply, ok",
    [
        (judgment((5, 5), (4, 3)), True),
        (judgment((5, 5)), False),  # a line missing
        (judgment((5, 5), (4, 3), (3, 3)), False),  # a line too many
        (judgment((5, 6), (4, 3)), False),  # off the scale
        (judgment((0, 5), (4, 3)), False),
    ],
)
def test_a_judgment_must_rate_every_line_once_on_the_scale(reply, ok):
    assert (valid_judgment(reply, 2) is not None) == ok


# --- Judging lines repeatedly ------------------------------------------------------------


def test_lines_are_queued_once_with_what_the_rater_sees(conn):
    first = queue_poem(conn, "run1", POEM)
    assert queue_poem(conn, "run1", POEM) == first  # already queued: the same items
    content = translation.json.loads(
        conn.execute("SELECT content FROM eval_items WHERE item_id = ?", (first[1],)).fetchone()[0]
    )
    assert content == {
        "poem": "Rima XXIII",
        "line_no": 2,
        "spanish": "Por una sonrisa, un cielo;",
        "natural_en": "For one smile, a heaven;",
        "literal_en": "For a smile, a sky;",
        "note_en": "Cielo is both sky and heaven.",
    }


def test_each_repeat_is_a_row_per_line_and_criterion(conn):
    items = queue_poem(conn, "run1", POEM)
    ask = scripted(judgment((5, 5), (4, 3)), judgment((5, 4), (4, 3)), judgment((5, 5), (3, 3)))
    run_id, calls = judge_poems(conn, [(POEM, items)], ask, repeats=3)

    assert len(calls) == 3
    rows = conn.execute(
        "SELECT item_id, criterion, repeat_no, score, rater FROM ratings WHERE run_id = ? "
        "ORDER BY item_id, criterion, repeat_no",
        (run_id,),
    ).fetchall()
    assert len(rows) == 2 * 2 * 3  # lines x criteria x repeats
    assert {r[4] for r in rows} == {"claude-sonnet-5-5"}
    first_line_faithfulness = [r[3] for r in rows if r[0] == items[0] and r[1] == "faithfulness"]
    assert first_line_faithfulness == [5, 4, 5]
    tokens = conn.execute("SELECT SUM(input_tokens), SUM(output_tokens) FROM ratings").fetchone()
    assert tuple(tokens) == (2700, 900)  # each call's tokens counted once


def test_an_invalid_judgment_is_retried_once_then_skipped(conn):
    items = queue_poem(conn, "run1", POEM)
    logged = []
    ask = scripted(
        judgment((5, 5)), judgment((5, 5), (4, 4)), judgment((9, 9), (1, 1)), judgment((5, 5))
    )
    run_id, calls = judge_poems(conn, [(POEM, items)], ask, repeats=2, log=logged.append)
    assert len(calls) == 4
    repeats = {
        r[0] for r in conn.execute("SELECT repeat_no FROM ratings WHERE run_id = ?", (run_id,))
    }
    assert repeats == {1}  # repeat 2 failed twice
    assert logged == ["  Rima XXIII, repeat 2: no valid judgment, skipped"]


# --- Consistency -------------------------------------------------------------------------


def test_consistency_counts_agreement_and_alpha_per_criterion(conn):
    items = queue_poem(conn, "run1", POEM)
    ask = scripted(*[judgment((5, 5), (3, 2))] * 3)  # the same scores every time
    run_id, _ = judge_poems(conn, [(POEM, items)], ask, repeats=3)
    found = consistency(conn, run_id)
    assert found["naturalness"]["all_agree"] == 2 and found["naturalness"]["within_one"] == 2
    assert found["naturalness"]["alpha"] == 1
    assert found["faithfulness"]["mean"] == pytest.approx(3.5)


def test_a_judge_that_flips_has_lower_alpha(conn):
    items = queue_poem(conn, "run1", POEM)
    ask = scripted(judgment((5, 5), (2, 2)), judgment((2, 5), (5, 2)), judgment((5, 5), (2, 2)))
    run_id, _ = judge_poems(conn, [(POEM, items)], ask, repeats=3)
    found = consistency(conn, run_id)
    assert found["faithfulness"]["alpha"] == 1
    assert found["naturalness"]["alpha"] < 0.5
    assert found["naturalness"]["all_agree"] == 0 and found["naturalness"]["within_one"] == 0


# --- Attempt verdicts --------------------------------------------------------------------


@dataclass
class Compared:
    line_no: int
    verdict: str | None
    comment_en: str | None = "ok"


class FakeSession:
    """Stands in for a LyricsSession: answers attempts from a script of verdicts."""

    def __init__(self, title, units, verdicts):
        self.item = {"title": title}
        self.units = units
        self.verdicts = list(verdicts)
        self.calls = []

    def attempt(self, attempts):
        self.calls.append(attempts)
        verdict = self.verdicts.pop(0)
        return [
            Compared(n, verdict if n in attempts else None) for n in range(1, len(self.units) + 1)
        ]


def test_attempts_are_sent_by_quality_and_repeated(conn):
    attempts = [
        Attempt(56, 4, "Today I believe in God!", "right"),
        Attempt(56, 4, "I believe in God!", "close"),
        Attempt(56, 4, "Today I created God!", "missed"),
    ]
    units = ["l1", "l2", "l3", "¡Hoy creo en Dios!"]
    # Two repeats of right/close/missed: the second time it calls "close" right.
    session = FakeSession(
        "Rima XVII", units, ["right", "close", "missed", "right", "right", "missed"]
    )
    run_id = judge_attempts(conn, {56: session}, repeats=2, attempts=attempts)

    assert session.calls[:3] == [
        {4: "Today I believe in God!"},
        {4: "I believe in God!"},
        {4: "Today I created God!"},
    ]
    found = consistency(conn, run_id)["verdict"]
    assert (found["items"], found["repeats"], found["all_agree"]) == (3, 2, 2)
    assert found["matches_intended"] == (5, 6)
    assert found["by_intended"]["close"] == ["close", "right"]


def test_the_attempts_cover_six_lines_at_three_qualities():
    assert len(ATTEMPTS) == 18
    lines = {(a.content_id, a.line_no) for a in ATTEMPTS}
    assert len(lines) == 6
    assert all(
        sorted(a.intended for a in ATTEMPTS if (a.content_id, a.line_no) == line)
        == ["close", "missed", "right"]
        for line in lines
    )
