"""Evaluation metrics over the log (sql/queries/eval_*.sql): built histories, known answers."""

import pytest

from spanish_tutor.evaluation.metrics import (
    Rate,
    adherence,
    completeness,
    completeness_gaps,
    format_report,
    reading,
    recommendation,
    report,
    wilson,
)

T0, T1, T2, T3 = (
    "2026-10-01 10:00:00",
    "2026-10-02 10:00:00",
    "2026-10-03 10:00:00",
    "2026-10-04 10:00:00",
)


# --- Building a history ---------------------------------------------------------------------


def word(conn, lemma):
    return conn.execute("INSERT INTO lexemes (lemma, pos) VALUES (?, 'NOUN')", (lemma,)).lastrowid


def session(conn, skill="conversation", at=T1):
    return conn.execute(
        "INSERT INTO sessions (skill, model, started_at) VALUES (?, 'm', ?)", (skill, at)
    ).lastrowid


def turn(conn, session_id, turn_no, role="tutor", kind="conversation", draft=None, final=None,
         retried=None, text="..."):  # fmt: skip
    return conn.execute(
        "INSERT INTO turns (session_id, turn_no, role, kind, text_es, draft_out_of_bank, "
        "final_out_of_bank, retried) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (session_id, turn_no, role, kind, text, draft, final, retried),
    ).lastrowid


def taught(conn, lexeme_id, turn_id=None, source="conversation", at=T1):
    conn.execute(
        "INSERT INTO word_events (lexeme_id, mode, event_type, source, turn_id, occurred_at) "
        "VALUES (?, 'recognition', 'taught', ?, ?, ?)",
        (lexeme_id, source, turn_id, at),
    )


def item(conn, title="El pollo", tokens=100):
    return conn.execute(
        "INSERT INTO content_items (kind, title, source, is_private, tokens, text_es) "
        "VALUES ('story', ?, 'test', 0, ?, '...')",
        (title, tokens),
    ).lastrowid


def vocab(conn, content_id, lexeme_id, occurrences):
    conn.execute("INSERT INTO content_vocab VALUES (?, ?, ?)", (content_id, lexeme_id, occurrences))


def content_event(conn, content_id, event, session_id=None, at=T2, chosen_via=None):
    if event == "started" and chosen_via is None:
        chosen_via = "recommended"
    conn.execute(
        "INSERT INTO content_events (content_id, event, chosen_via, session_id, occurred_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (content_id, event, chosen_via, session_id, at),
    )


# --- Wilson intervals -----------------------------------------------------------------------


def test_wilson_interval_known_values():
    assert wilson(0, 0) is None
    low, high = wilson(25, 25)
    assert (round(low, 3), high) == (0.867, 1.0)  # all 25 replies: still not certain
    low, high = wilson(5, 5)
    assert round(low, 3) == 0.566
    low, high = wilson(50, 100)
    assert (round(low, 3), round(high, 3)) == (0.404, 0.596)


def test_a_rate_of_nothing_says_so():
    assert Rate(0, 0).value is None and str(Rate(0, 0)) == "no data (n = 0)"
    assert str(Rate(3, 4)).startswith("75% (3/4; 95% CI")


# --- Adherence ------------------------------------------------------------------------------


def test_adherence_counts_conversation_replies_by_skill(conn):
    chat, talk = session(conn), session(conn, skill="reading")
    turn(conn, chat, 1, draft=0, final=0, retried=0)
    turn(conn, chat, 2, draft=1, final=1, retried=0)
    turn(conn, chat, 3, draft=3, final=1, retried=1)  # rescued by the retry
    turn(conn, chat, 4, draft=2, final=2, retried=1)  # not rescued
    turn(conn, chat, 5, kind="translation", draft=None, final=None)  # asked for: not counted
    turn(conn, chat, 6, role="learner")
    turn(conn, talk, 1, draft=0, final=0, retried=0)

    found = adherence(conn)
    assert list(found) == ["conversation", "reading", "all"]
    chat_rates = found["conversation"]
    assert chat_rates["replies"] == 4
    assert (chat_rates["within_limit_draft"].k, chat_rates["within_limit_final"].k) == (2, 3)
    assert (chat_rates["retried"].k, chat_rates["no_new_words"].k) == (2, 1)
    assert chat_rates["new_words_shown"] == 4
    assert found["all"]["replies"] == 5
    assert adherence(conn, session_id=talk)["all"]["replies"] == 1


# --- Completeness ---------------------------------------------------------------------------


def test_completeness_counts_words_taught_on_the_reply_itself(conn):
    chat = session(conn)
    perro, gato, mesa, sol = (word(conn, w) for w in ["perro", "gato", "mesa", "sol"])
    opening = turn(conn, chat, 1, draft=0, final=0)
    taught(conn, mesa, opening, source="pre_teach")  # topic word: not part of the reply
    complete = turn(conn, chat, 2, draft=1, final=1)
    taught(conn, perro, complete)
    short = turn(conn, chat, 3, draft=2, final=2)
    taught(conn, gato, short)  # the second flagged word was never taught
    lookup = turn(conn, chat, 4, kind="study")
    taught(conn, sol, lookup)  # a lookup: its own turn, not a reply
    turn(conn, chat, 5, draft=0, final=0)

    found = completeness(conn)["conversation"]
    assert found["replies"] == 4
    assert (found["complete"].k, found["complete"].n) == (1, 2)
    assert (found["gaps"], found["words_flagged"], found["words_taught"]) == (1, 3, 2)
    (gap,) = completeness_gaps(conn)
    assert (gap["turn_no"], gap["flagged"], gap["taught"], gap["taught_words"]) == (3, 2, 1, "gato")


def test_a_lookup_on_the_last_reply_would_show_as_a_gap(conn):
    # Why lookups got their own turn: logged on the reply, a lookup looks like over-teaching.
    chat = session(conn)
    perro = word(conn, "perro")
    reply = turn(conn, chat, 1, draft=0, final=0)
    taught(conn, perro, reply)
    assert completeness(conn)["conversation"]["gaps"] == 1


# --- Reading --------------------------------------------------------------------------------


def test_reading_compares_the_index_with_what_was_taught(conn):
    story = item(conn)
    casa, perro, gato, rio, sol = (word(conn, w) for w in ["casa", "perro", "gato", "río", "sol"])
    for lexeme, occurrences in [(casa, 10), (perro, 5), (gato, 3), (rio, 2)]:
        vocab(conn, story, lexeme, occurrences)
    taught(conn, casa, at=T0)  # known before the session: not predicted new
    reader = session(conn, skill="reading", at=T2)
    content_event(conn, story, "started", reader, at=T2)
    batch = turn(conn, reader, 1, kind="study")
    taught(conn, perro, batch, source="reading", at=T2)
    taught(conn, sol, batch, source="reading", at=T2)  # not in the index: unpredicted
    turn(conn, reader, 2, role="learner", kind="reading")  # finished
    late = turn(conn, reader, 3, kind="study")
    taught(conn, gato, late, source="reading", at=T2)  # after finishing: not "before"

    (row,) = reading(conn)["sessions"]
    assert row["finished"] == 1
    assert (row["predicted_new"], row["studied"], row["taught_unpredicted"]) == (3, 1, 1)
    assert (row["new_tokens"], row["studied_tokens"]) == (10, 5)
    found = reading(conn)
    assert (found["studied_before_finishing"].k, found["studied_before_finishing"].n) == (1, 3)
    assert (found["index_agreement"].k, found["index_agreement"].n) == (1, 2)


def test_an_unfinished_reading_counts_everything_taught_so_far(conn):
    story = item(conn)
    perro = word(conn, "perro")
    vocab(conn, story, perro, 4)
    reader = session(conn, skill="reading", at=T2)
    content_event(conn, story, "started", reader, at=T2)
    taught(conn, perro, turn(conn, reader, 1, kind="study"), source="reading", at=T2)

    (row,) = reading(conn)["sessions"]
    assert (row["finished"], row["predicted_new"], row["studied"]) == (0, 1, 1)
    assert reading(conn)["studied_before_finishing"].n == 0  # only finished sessions count


# --- Recommendation -------------------------------------------------------------------------


def test_recommendation_take_rate_and_finishing_by_difficulty(conn):
    easy, hard = item(conn, "Fácil", tokens=100), item(conn, "Difícil", tokens=100)
    casa, perro = word(conn, "casa"), word(conn, "perro")
    vocab(conn, easy, casa, 97)
    vocab(conn, easy, perro, 3)  # 3% unknown
    vocab(conn, hard, perro, 30)  # 30% unknown
    vocab(conn, hard, casa, 70)
    taught(conn, casa, at=T0)

    first, second, third = (
        session(conn, "reading", T1),
        session(conn, "reading", T2),
        session(conn, "reading", T3),
    )
    content_event(conn, easy, "started", first, at=T1)
    content_event(conn, easy, "finished", first, at=T1)
    content_event(conn, hard, "started", second, at=T2, chosen_via="requested")
    taught(conn, perro, at=T2)  # learned in between: the easy item is now fully known
    content_event(conn, easy, "started", third, at=T3)

    found = recommendation(conn)
    assert found["starts"] == 3
    assert (found["take_rate"].k, found["take_rate"].n) == (2, 3)
    bands = found["finished_by_band"]
    assert (bands["0-5%"].k, bands["0-5%"].n) == (1, 2)  # easy, twice; finished once
    assert (bands["20%+"].k, bands["20%+"].n) == (0, 1)
    shares = {(g["chosen_via"], g["band"]): g["mean_unknown_share"] for g in found["groups"]}
    assert shares[("requested", "20%+")] == 0.3
    assert shares[("recommended", "0-5%")] == pytest.approx(0.015)  # 3% then 0%


def test_a_finish_in_another_session_doesnt_count(conn):
    story = item(conn)
    first, second = session(conn, "reading", T1), session(conn, "reading", T2)
    content_event(conn, story, "started", first, at=T1)
    content_event(conn, story, "finished", second, at=T2)
    assert recommendation(conn)["finished_by_band"]["0-5%"].k == 0


# --- The report -----------------------------------------------------------------------------


def test_the_report_runs_on_an_empty_log_and_says_there_is_no_data(conn):
    text = format_report(report(conn))
    assert "no tutor replies yet" in text
    assert "no data (n = 0)" in text
