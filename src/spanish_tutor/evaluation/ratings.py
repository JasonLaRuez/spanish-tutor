"""Jason's own ratings (the rating page): what each kind of item is rated on, the queue,
recording a rating, and what the ratings say.

Ratings go in the append-only `ratings` table with rater 'human' and no run; changing one
adds a row, and the latest counts (sql/queries/eval_rating_queue.sql).
"""

import json
import sqlite3
from dataclasses import dataclass

from spanish_tutor.evaluation.metrics import Rate
from spanish_tutor.progress import rows


@dataclass(frozen=True)
class Criterion:
    name: str  # ratings.criterion
    question: str  # what the page asks
    labels: tuple[str, ...] = ()  # a choice of categories...
    scale: tuple[int, int] | None = None  # ...or a score range


CRITERIA = {
    # Was a word the tutor taught as new really new to the learner? The new-word
    # detector's precision. "not_a_word": the tagger made it up or split it wrongly.
    "new_word_flag": Criterion(
        "truly_new",
        "Did you already know this word, as it's used in the sentence?",
        labels=("new", "known", "not_a_word"),
    ),
    # How natural the English of a translated line is, as Jason (a native speaker) hears
    # it: the calibration set for the translation judge (slice 4.4d).
    "translation_line": Criterion(
        "naturalness",
        "How natural is this English, as an English sentence?",
        scale=(1, 5),
    ),
    # The lexicon review (lexicon_review.py): a field the reviewer flagged. Jason's answer is
    # both the reviewer's precision and his decision: only "fix" is applied.
    "lexeme_flag": Criterion(
        "flag_right",
        "Is the flagged problem real, and is the suggested fix right?",
        labels=("fix", "real_not_fix", "not_a_problem"),
    ),
    # A word the reviewer passed, sampled: is anything wrong with it? (Its miss rate.)
    "lexeme_entry": Criterion(
        "entry_ok",
        "Is this dictionary entry right for a learner?",
        labels=("ok", "wrong"),
    ),
}


class RatingError(ValueError):
    """A rating the item's criterion doesn't allow."""


def queue(conn: sqlite3.Connection, item_type: str) -> list[dict]:
    """Every item of a type with Jason's latest rating (None if unrated), unrated first."""
    criterion = CRITERIA[item_type]
    found = rows(conn, "eval_rating_queue", item_type=item_type, criterion=criterion.name)
    for row in found:
        row["content"] = json.loads(row["content"])
    return found


def rate(
    conn: sqlite3.Connection,
    item_id: int,
    label: str | None = None,
    score: float | None = None,
) -> None:
    """Record Jason's rating of an item, checked against its criterion."""
    found = conn.execute(
        "SELECT item_type FROM eval_items WHERE item_id = ?", (item_id,)
    ).fetchone()
    if found is None:
        raise RatingError(f"No item {item_id}.")
    criterion = CRITERIA[found[0]]
    if criterion.labels and (label not in criterion.labels or score is not None):
        raise RatingError(f"Rate it as one of: {', '.join(criterion.labels)}.")
    if criterion.scale:
        low, high = criterion.scale
        if label is not None or score is None or not low <= score <= high:
            raise RatingError(f"Rate it from {low} to {high}.")
    with conn:
        conn.execute(
            "INSERT INTO ratings (item_id, criterion, rater, score, label) "
            "VALUES (?, ?, 'human', ?, ?)",
            (item_id, criterion.name, score, label),
        )


def progress(conn: sqlite3.Connection) -> dict[str, dict]:
    """Per item type: how many items there are and how many Jason has rated."""
    result = {}
    for item_type in CRITERIA:
        items = queue(conn, item_type)
        result[item_type] = {
            "items": len(items),
            "rated": sum(1 for i in items if i["label"] is not None or i["score"] is not None),
        }
    return result


def new_word_precision(conn: sqlite3.Connection) -> dict:
    """The new-word detector's precision: of the words taught as new that Jason rated, the
    share that really were new to him. Split by where they came from: the real log
    (source_ref 'turn:...') or a benchmark run ('benchmark:...')."""
    rated = [i for i in queue(conn, "new_word_flag") if i["label"] is not None]

    def precision(items: list[dict]) -> Rate:
        return Rate(sum(1 for i in items if i["label"] == "new"), len(items))

    return {
        "all": precision(rated),
        "real": precision([i for i in rated if not i["source_ref"].startswith("benchmark:")]),
        "benchmark": precision([i for i in rated if i["source_ref"].startswith("benchmark:")]),
        "labels": {
            label: sum(1 for i in rated if i["label"] == label)
            for label in CRITERIA["new_word_flag"].labels
        },
        "false_flags": [i["content"]["lemma"] for i in rated if i["label"] != "new"],
    }
