"""Evaluation metrics computed from the learning log with the SQL in sql/queries/eval_*.sql.

The queries return counts; this module turns them into rates, each with its n and a 95%
Wilson score interval. The Wilson interval rather than the normal approximation because n
is small (25 replies in the first two real sessions) and rates sit near 0 or 1, where
the normal interval leaves [0, 1] or collapses to zero width.
"""

import math
import sqlite3
from dataclasses import dataclass

from spanish_tutor.progress import rows

Z95 = 1.959964


@dataclass(frozen=True)
class Rate:
    """k of n, with a 95% Wilson interval. A rate of nothing (n = 0) is None, not 0."""

    k: int
    n: int

    @property
    def value(self) -> float | None:
        return self.k / self.n if self.n else None

    @property
    def interval(self) -> tuple[float, float] | None:
        return wilson(self.k, self.n)

    def __str__(self) -> str:
        if not self.n:
            return "no data (n = 0)"
        low, high = self.interval
        return f"{self.value:.0%} ({self.k}/{self.n}; 95% CI {low:.0%}-{high:.0%})"


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    """The Wilson score interval for k successes in n trials (None when n = 0)."""
    if n == 0:
        return None
    p = k / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    denominator = 1 + z * z / n
    return max(0.0, (centre - spread) / denominator), min(1.0, (centre + spread) / denominator)


def _by_skill(found: list[dict]) -> dict[str, dict]:
    return {row["skill"]: row for row in found}


def adherence(
    conn: sqlite3.Connection, session_id: int | None = None, from_session: int | None = None
) -> dict[str, dict]:
    """Per skill (and 'all'): replies within the one-new-word limit, draft and final."""
    result = {}
    for skill, row in _by_skill(
        rows(conn, "eval_adherence", session_id=session_id, from_session=from_session)
    ).items():
        n = row["replies"]
        result[skill] = {
            "replies": n,
            "within_limit_draft": Rate(row["within_limit_draft"], n),
            "within_limit_final": Rate(row["within_limit_final"], n),
            "retried": Rate(row["retried"], n),
            "no_new_words": Rate(row["no_new_words"], n),
            "new_words_shown": row["new_words_shown"],
            "mean_new_words_draft": row["mean_new_words_draft"],
        }
    return result


def completeness(
    conn: sqlite3.Connection, session_id: int | None = None, from_session: int | None = None
) -> dict[str, dict]:
    """Per skill (and 'all'): replies with new words that taught every one of them."""
    result = {}
    for skill, row in _by_skill(
        rows(conn, "eval_completeness", session_id=session_id, from_session=from_session)
    ).items():
        result[skill] = {
            "replies": row["replies"],
            "complete": Rate(row["complete"], row["replies_with_new_words"]),
            "gaps": row["gaps"],
            "words_flagged": row["words_flagged"],
            "words_taught": row["words_taught"],
        }
    return result


def completeness_gaps(
    conn: sqlite3.Connection, session_id: int | None = None, from_session: int | None = None
) -> list[dict]:
    return rows(conn, "eval_completeness_gaps", session_id=session_id, from_session=from_session)


def reading(
    conn: sqlite3.Connection, session_id: int | None = None, from_session: int | None = None
) -> dict:
    """Reading and song sessions: the index's prediction against what was taught.

    studied_before_finishing: of the words predicted new, the share taught before the
    learner finished (finished sessions only). index_agreement: of the words taught in
    the sessions, the share the index had predicted.
    """
    sessions = rows(conn, "eval_reading", session_id=session_id, from_session=from_session)
    finished = [s for s in sessions if s["finished"]]
    taught = sum(s["studied"] + s["taught_unpredicted"] for s in sessions)
    return {
        "sessions": sessions,
        "finished": len(finished),
        "studied_before_finishing": Rate(
            sum(s["studied"] for s in finished), sum(s["predicted_new"] for s in finished)
        ),
        "index_agreement": Rate(sum(s["studied"] for s in sessions), taught),
    }


def recommendation(conn: sqlite3.Connection, from_session: int | None = None) -> dict:
    """The take rate of the default suggestion, and finishing by difficulty band."""
    groups = rows(conn, "eval_recommendation", from_session=from_session)
    starts = sum(g["starts"] for g in groups)
    recommended = sum(g["starts"] for g in groups if g["chosen_via"] == "recommended")
    bands: dict[str, list[int]] = {}
    for g in groups:
        k_n = bands.setdefault(g["band"], [0, 0])
        k_n[0] += g["finished"]
        k_n[1] += g["starts"]
    return {
        "groups": groups,
        "starts": starts,
        "take_rate": Rate(recommended, starts),
        "finished_by_band": {band: Rate(k, n) for band, (k, n) in bands.items()},
    }


def report(
    conn: sqlite3.Connection, session_id: int | None = None, from_session: int | None = None
) -> dict:
    """Every log metric. `from_session` keeps sessions from that one on (a benchmark run's
    own, on a copy that also holds the real history)."""
    return {
        "adherence": adherence(conn, session_id, from_session),
        "completeness": completeness(conn, session_id, from_session),
        "completeness_gaps": completeness_gaps(conn, session_id, from_session),
        "reading": reading(conn, session_id, from_session),
        "recommendation": recommendation(conn, from_session),
    }


def format_report(found: dict) -> str:
    lines = ["Vocabulary adherence (tutor replies within one new word)"]
    for skill, a in found["adherence"].items():
        lines.append(
            f"  {skill:12} first draft {a['within_limit_draft']}; shown {a['within_limit_final']}; "
            f"retried {a['retried']}"
        )
    if not found["adherence"]:
        lines.append("  no tutor replies yet")
    lines.append("Teaching completeness (replies with new words that taught them all)")
    for skill, c in found["completeness"].items():
        lines.append(f"  {skill:12} {c['complete']}; {c['gaps']} replies with a gap")
    for gap in found["completeness_gaps"]:
        lines.append(
            f"    session {gap['session_id']} turn {gap['turn_no']}: flagged {gap['flagged']}, "
            f"taught {gap['taught']} ({gap['taught_words'] or 'none'})"
        )
    r = found["reading"]
    lines.append(f"Reading and songs ({len(r['sessions'])} sessions, {r['finished']} finished)")
    lines.append(f"  new words studied before finishing: {r['studied_before_finishing']}")
    lines.append(f"  words taught that the index predicted: {r['index_agreement']}")
    rec = found["recommendation"]
    lines.append(f"Recommendation ({rec['starts']} items started)")
    lines.append(f"  took the default suggestion: {rec['take_rate']}")
    for band, rate in rec["finished_by_band"].items():
        lines.append(f"  finished, {band:6} unknown: {rate}")
    return "\n".join(lines)
