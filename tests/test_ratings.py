"""Jason's hand ratings: the queue, recording a rating, and the detector's precision."""

import json

import pytest

from spanish_tutor.evaluation import ratings


def item(conn, ref, item_type="new_word_flag", **content):
    content = content or {"lemma": ref}
    return conn.execute(
        "INSERT INTO eval_items (item_type, source_ref, content) VALUES (?, ?, ?)",
        (item_type, ref, json.dumps(content)),
    ).lastrowid


def test_the_queue_puts_unrated_items_first_in_queued_order(conn):
    first, second, third = (item(conn, ref) for ref in ["turn:1", "turn:2", "turn:3"])
    ratings.rate(conn, second, label="known")
    queued = ratings.queue(conn, "new_word_flag")
    assert [i["item_id"] for i in queued] == [first, third, second]
    assert queued[0]["content"] == {"lemma": "turn:1"} and queued[0]["label"] is None
    assert queued[2]["label"] == "known"


def test_a_changed_rating_is_a_new_row_and_the_latest_counts(conn):
    word = item(conn, "turn:1")
    ratings.rate(conn, word, label="known")
    ratings.rate(conn, word, label="new")
    assert ratings.queue(conn, "new_word_flag")[0]["label"] == "new"
    assert conn.execute("SELECT COUNT(*) FROM ratings").fetchone()[0] == 2  # append-only


@pytest.mark.parametrize(
    "item_type, label, score",
    [
        ("new_word_flag", "maybe", None),  # not one of its labels
        ("new_word_flag", None, 3),  # a score where a label is asked for
        ("translation_line", None, 6),  # outside 1-5
        ("translation_line", "good", None),  # a label where a score is asked for
    ],
)
def test_a_rating_the_criterion_doesnt_allow_is_refused(conn, item_type, label, score):
    target = item(conn, "x", item_type=item_type)
    with pytest.raises(ratings.RatingError):
        ratings.rate(conn, target, label=label, score=score)
    assert conn.execute("SELECT COUNT(*) FROM ratings").fetchone()[0] == 0


def test_rating_an_item_that_doesnt_exist_is_refused(conn):
    with pytest.raises(ratings.RatingError, match="No item"):
        ratings.rate(conn, 99, label="new")


def test_translation_lines_are_scored_one_to_five(conn):
    line = item(conn, "song_translation_lines:1:1", item_type="translation_line", natural_en="Hi")
    ratings.rate(conn, line, score=4)
    assert ratings.queue(conn, "translation_line")[0]["score"] == 4


def test_precision_is_the_share_of_taught_words_that_were_really_new(conn):
    labels = {
        "turn:1": "new",
        "turn:2": "known",
        "benchmark:r:turn:3": "new",
        "benchmark:r:turn:4": "not_a_word",
        "benchmark:r:turn:5": None,  # not rated yet: left out
    }
    for ref, label in labels.items():
        target = item(conn, ref, lemma=ref.split(":")[-1])
        if label:
            ratings.rate(conn, target, label=label)

    found = ratings.new_word_precision(conn)
    assert (found["all"].k, found["all"].n) == (2, 4)
    assert (found["real"].k, found["real"].n) == (1, 2)
    assert (found["benchmark"].k, found["benchmark"].n) == (1, 2)
    assert found["labels"] == {"new": 2, "known": 1, "not_a_word": 1}
    assert sorted(found["false_flags"]) == ["2", "4"]
    assert ratings.progress(conn)["new_word_flag"] == {"items": 5, "rated": 4}
