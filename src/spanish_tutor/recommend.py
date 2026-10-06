"""The comprehensible-input recommender (roadmap Phase 3): what to read or listen to next.

Two rankings, both plain SQL over the difficulty index and the word bank
(sql/queries/recommend_*.sql):

- songs and short stories, by the distinct words each would teach, fewest first;
- books, by new-word density across the whole book, with the next chapter of a started
  book always ahead of starting another one.

The first row of each is the default suggestion. The learner can always ask for anything
else instead (content.start(..., 'requested')), and that choice is logged.
"""

import random
import sqlite3

from spanish_tutor.progress import rows

SURPRISE_FROM = 5  # "Surprise me" picks among this many of the easiest songs and stories


def items(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Songs and stories not finished yet, fewest new words first."""
    return rows(conn, "recommend_items", limit=limit)


def books(conn: sqlite3.Connection) -> list[dict]:
    """Unfinished books, each with its next chapter: started books first, then by density."""
    return rows(conn, "recommend_books")


def surprise(conn: sqlite3.Connection, rng: random.Random | None = None) -> dict | None:
    """A random pick that keeps to i+1 (Jason's choice, 2026-10-05).

    The candidates are the SURPRISE_FROM easiest songs and stories, plus the next chapter of
    every book in progress (never the start of a new book, and never a chapter out of
    order). Returns {content_id, kind, title, new_words}, or None when there is nothing.
    """
    candidates = [
        {k: row[k] for k in ("content_id", "kind", "title", "new_words")}
        for row in items(conn, SURPRISE_FROM)
    ]
    candidates += [
        {
            "content_id": book["next_content_id"],
            "kind": "chapter",
            "title": f"{book['title']}, {book['next_chapter_title']}",
            "new_words": book["next_chapter_new_words"],
        }
        for book in books(conn)
        if book["state"] == "in progress"
    ]
    return (rng or random).choice(candidates) if candidates else None


def catalog(conn: sqlite3.Connection) -> list[dict]:
    """Every item with its new-word count and reading state, to choose one yourself."""
    found = rows(conn, "content_catalog")
    for item in found:
        item["indexed"] = bool(item["indexed"])
    return found


def item(conn: sqlite3.Connection, content_id: int) -> dict | None:
    """One item for the reader (text, book position, reading state); None if it doesn't exist."""
    found = rows(conn, "content_item", content_id=content_id)
    if not found:
        return None
    detail = found[0]
    for flag in ("indexed", "started", "finished"):
        detail[flag] = bool(detail[flag])
    return detail


def new_words(conn: sqlite3.Connection, content_id: int) -> list[dict]:
    """The words an item would teach, in pre-teaching order."""
    found = rows(conn, "item_new_words", content_id=content_id)
    for word in found:
        word["model_written"] = bool(word["model_written"])
    return found
