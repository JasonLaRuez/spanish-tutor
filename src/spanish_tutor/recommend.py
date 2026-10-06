"""The comprehensible-input recommender (roadmap Phase 3): what to read or listen to next.

Two rankings, both plain SQL over the difficulty index and the word bank
(sql/queries/recommend_*.sql):

- songs and short stories, by the distinct words each would teach, fewest first;
- books, by new-word density across the whole book, with the next chapter of a started
  book always ahead of starting another one.

The first row of each is the default suggestion. The learner can always ask for anything
else instead (content.start(..., 'requested')), and that choice is logged.
"""

import sqlite3

from spanish_tutor.progress import rows


def items(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Songs and stories not finished yet, fewest new words first."""
    return rows(conn, "recommend_items", limit=limit)


def books(conn: sqlite3.Connection) -> list[dict]:
    """Unfinished books, each with its next chapter: started books first, then by density."""
    return rows(conn, "recommend_books")


def new_words(conn: sqlite3.Connection, content_id: int) -> list[dict]:
    """The words an item would teach, in pre-teaching order."""
    found = rows(conn, "item_new_words", content_id=content_id)
    for word in found:
        word["model_written"] = bool(word["model_written"])
    return found
