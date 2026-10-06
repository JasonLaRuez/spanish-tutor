"""Content items and the content-difficulty index (roadmap Phase 3).

A content item is a song, a short story, or a chapter of a book. Indexing an item records
its full vocabulary, with how often each word occurs, in content_vocab; the recommender
then ranks items with one SQL join against the word bank (sql/queries/recommend_*.sql)
instead of re-analyzing any text.

Indexing reuses lexicon.analyze, so the index and the word bank agree on what a word is.
Each analysis is resolved to a lexeme, first match wins:

1. a word already in `lexemes` (exact, or with accents restored: LexiconIndex.find);
2. a word Wiktionary knows, or an approved expression, added to `lexemes`;
3. an earlier decision for the same form, stored in word_resolutions;
4. otherwise the form is pending, and all of an item's pending forms go to the resolver
   (resolve.py) together; without one, they count as unresolved.

Unresolved words (not Spanish, or not resolvable) are excluded from the vocabulary and
counted in content_items.unresolved_tokens, so the exclusion stays visible.

Nothing is written until the analysis and any model call are done; then the item's whole
index is replaced in one transaction, so a failed call leaves the item as it was.
"""

import argparse
import csv
import json
import re
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from spanish_tutor import db
from spanish_tutor.config import DATA_DIR
from spanish_tutor.lexicon import Analysis, TokenAnalysis
from spanish_tutor.resolve import MAX_FORMS_PER_CALL, Asked, Outcome, Pending, Resolver, apply
from spanish_tutor.words import LexiconIndex

AnalyzeMany = Callable[[list[str]], Iterable[list[TokenAnalysis]]]
Key = tuple[str, str, str]  # (form as written, lemma, pos)

MAX_CONTEXT = 400  # characters of a context sentence sent to the resolver and stored
RESOLUTIONS_CSV = DATA_DIR / "processed" / "content_resolutions.csv"

# Claude Opus 5.5 list prices, $ per million tokens, for the estimate shown before a call.
PRICE_IN, PRICE_OUT = 4.0, 20.0
# Size of a resolution request, measured on the live test (2026-10-05: 4 forms, 1,524 in /
# 296 out): ~1,350 tokens per call (instructions + output schema), ~45 per form in, ~75 per
# form out (thinking included); output is rounded up to 100 for margin.
EST_PROMPT_TOKENS, EST_TOKENS_PER_FORM_IN, EST_TOKENS_PER_FORM_OUT = 1350, 45, 100


# --- Adding content ---------------------------------------------------------------------


def add_item(
    conn: sqlite3.Connection,
    kind: str,
    title: str,
    text: str,
    *,
    source: str,
    is_private: bool,
    author: str | None = None,
) -> int:
    """Add a song or a story (not yet indexed); returns its content_id."""
    if kind not in ("song", "story"):
        raise ValueError(f"add_item adds songs and stories, not {kind!r}: use add_book")
    return conn.execute(
        """
        INSERT INTO content_items (kind, title, author, source, is_private, text_es)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (kind, title, author, source, int(is_private), text),
    ).lastrowid


def add_book(
    conn: sqlite3.Connection,
    title: str,
    chapters: Sequence[tuple[str, str]],
    *,
    source: str,
    is_private: bool,
    author: str | None = None,
) -> tuple[int, list[int]]:
    """Add a book and its chapters, given in reading order as (title, text) pairs."""
    book_id = conn.execute(
        "INSERT INTO books (title, author, source, is_private) VALUES (?, ?, ?, ?)",
        (title, author, source, int(is_private)),
    ).lastrowid
    ids = [
        conn.execute(
            """
            INSERT INTO content_items (kind, title, book_id, chapter_no, text_es)
            VALUES ('chapter', ?, ?, ?, ?)
            """,
            (chapter_title, book_id, number, text),
        ).lastrowid
        for number, (chapter_title, text) in enumerate(chapters, 1)
    ]
    return book_id, ids


# --- Analysis ---------------------------------------------------------------------------

# A sentence ends after final punctuation followed by whitespace, or directly before an
# opening ¿ or ¡, which always starts a new sentence or clause ("queja...¿no?"): spaCy
# leaves "...¿no" as one token, so it's split here.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|(?<=[.!?…])(?=[¿¡])")
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def sentences(text: str, kind: str) -> list[str]:
    """The units a text is analyzed in: lines of a song, sentences of prose.

    Lyrics often have no punctuation, and a line is their natural unit. Prose is split
    into paragraphs at blank lines, and a paragraph's own line breaks are joined: books
    (Project Gutenberg's text files) are hard-wrapped at ~70 characters, and a half
    sentence tags badly ("y le jura no" made "no" a particle). Each paragraph is then split
    after sentence-final punctuation.
    """
    if kind == "song":
        return [line.strip() for line in text.splitlines() if line.strip()]
    paragraphs = (" ".join(p.split()) for p in _PARAGRAPH_BREAK.split(text))
    return [s.strip() for p in paragraphs if p for s in _SENTENCE_END.split(p) if s.strip()]


@dataclass
class Analyzed:
    """An item's words before anything is written: counts, and where each was met."""

    counts: Counter[Key] = field(default_factory=Counter)
    context: dict[Key, str] = field(default_factory=dict)


def analyze_text(text: str, kind: str, analyze_many: AnalyzeMany) -> Analyzed:
    """Count every (form, lemma, pos) in a text, per analysis: "del" counts de and el.

    Tokens with no analysis (names, the later words of an expression) aren't counted.
    """
    units = sentences(text, kind)
    analyzed = Analyzed()
    for unit, tokens in zip(units, analyze_many(units), strict=True):
        for form, analyses in tokens:
            for lemma, pos in analyses:
                key = (form, lemma, pos)
                analyzed.counts[key] += 1
                analyzed.context.setdefault(key, unit[:MAX_CONTEXT])
    return analyzed


def latest_resolution(conn: sqlite3.Connection, key: Key) -> tuple[bool, int | None]:
    """(found, lexeme_id) from the newest word_resolutions row for a form."""
    row = conn.execute(
        """
        SELECT lexeme_id FROM word_resolutions
        WHERE form = ? AND tagged_lemma = ? AND tagged_pos = ?
        ORDER BY resolution_id DESC
        LIMIT 1
        """,
        key,
    ).fetchone()
    return (False, None) if row is None else (True, row[0])


@dataclass
class Plan:
    """How each of an item's words will resolve. Built without writing anything."""

    analyzed: Analyzed
    found: dict[Key, int] = field(default_factory=dict)  # already lexemes
    dictionary: list[Key] = field(default_factory=list)  # to add from Wiktionary / expressions
    cached: dict[Key, int | None] = field(default_factory=dict)  # earlier resolutions
    pending: list[Pending] = field(default_factory=list)  # for the resolver


def plan_index(conn: sqlite3.Connection, index: LexiconIndex, analyzed: Analyzed) -> Plan:
    plan = Plan(analyzed)
    for key, n in analyzed.counts.items():
        analysis: Analysis = key[1], key[2]
        if (lexeme := index.find(analysis)) is not None:
            plan.found[key] = lexeme.lexeme_id
        elif index.in_dictionary(analysis):
            plan.dictionary.append(key)
        elif (cached := latest_resolution(conn, key))[0]:
            plan.cached[key] = cached[1]
        else:
            plan.pending.append(Pending(*key, analyzed.context[key], n))
    # Most frequent first: the order the resolver sees them and the review CSV lists them.
    plan.pending.sort(key=lambda p: (-p.occurrences, p.form))
    return plan


# --- Indexing ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Indexed:
    content_id: int
    tokens: int  # counted as vocabulary
    unresolved_tokens: int
    words: int  # distinct lexemes
    from_dictionary: int  # words added to lexemes from Wiktionary or the expression list
    from_cache: int  # forms resolved by an earlier decision, without a call
    outcomes: list[Outcome]  # this run's resolver results (or "unasked" without a resolver)
    asked: Asked | None  # the resolver call's verdicts and cost; None if no call


def item_kind(conn: sqlite3.Connection, content_id: int) -> tuple[str, str]:
    row = conn.execute(
        "SELECT kind, text_es FROM content_items WHERE content_id = ?", (content_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"no content item {content_id}")
    return row[0], row[1]


def index_item(
    conn: sqlite3.Connection,
    index: LexiconIndex,
    content_id: int,
    analyze_many: AnalyzeMany,
    analyzer: str,
    *,
    resolver: Resolver | None = None,
    reviewer: str | None = None,
    analyzed: Analyzed | None = None,
) -> Indexed:
    """(Re)build one item's entry in the difficulty index. See the module docstring.

    `analyzer` is the analysis setup's fingerprint, stored so stale_items can find items
    indexed under another one. `reviewer` names the resolver in word_resolutions
    ('model:<id>'). `analyzed` skips re-analysis when the caller already has it. If the
    transaction fails, rebuild `index`: it may hold rolled-back rows.
    """
    kind, text = item_kind(conn, content_id)
    analyzed = analyzed or analyze_text(text, kind, analyze_many)
    plan = plan_index(conn, index, analyzed)
    asked = resolver(kind, plan.pending) if resolver and plan.pending else None

    with conn:
        ids: dict[Key, int | None] = dict(plan.found) | plan.cached
        for key in plan.dictionary:
            lexeme = index.resolve((key[1], key[2]))
            ids[key] = lexeme.lexeme_id if lexeme else None
        if asked is not None:
            outcomes = apply(
                conn, index, content_id, plan.pending, asked.resolutions, reviewer or "unknown"
            )
        else:
            outcomes = [
                Outcome(p, "unasked", None, None, None, "no resolver") for p in plan.pending
            ]
        for outcome in outcomes:
            ids[outcome.pending.key] = outcome.lexeme_id

        occurrences: Counter[int] = Counter()
        unresolved = 0
        for key, n in plan.analyzed.counts.items():
            if (lexeme_id := ids.get(key)) is None:
                unresolved += n
            else:
                occurrences[lexeme_id] += n
        conn.execute("DELETE FROM content_vocab WHERE content_id = ?", (content_id,))
        conn.executemany(
            "INSERT INTO content_vocab (content_id, lexeme_id, occurrences) VALUES (?, ?, ?)",
            [(content_id, lexeme_id, n) for lexeme_id, n in sorted(occurrences.items())],
        )
        conn.execute(
            """
            UPDATE content_items
            SET tokens = ?, unresolved_tokens = ?, analyzer = ?, indexed_at = CURRENT_TIMESTAMP
            WHERE content_id = ?
            """,
            (occurrences.total(), unresolved, analyzer, content_id),
        )
    return Indexed(
        content_id,
        tokens=occurrences.total(),
        unresolved_tokens=unresolved,
        words=len(occurrences),
        from_dictionary=len(plan.dictionary),
        from_cache=len(plan.cached),
        outcomes=outcomes,
        asked=asked,
    )


def stale_items(conn: sqlite3.Connection, analyzer: str) -> list[int]:
    """Items never indexed, or indexed under another analysis setup: the re-index job."""
    return [
        row[0]
        for row in conn.execute(
            """
            SELECT content_id FROM content_items
            WHERE indexed_at IS NULL OR analyzer IS NOT ?
            ORDER BY content_id
            """,
            (analyzer,),
        )
    ]


def estimate_cost(forms: int) -> float:
    """Dollars for resolving `forms` forms, from the per-form sizes above."""
    calls = -(-forms // MAX_FORMS_PER_CALL)
    tokens_in = calls * EST_PROMPT_TOKENS + forms * EST_TOKENS_PER_FORM_IN
    return (tokens_in * PRICE_IN + forms * EST_TOKENS_PER_FORM_OUT * PRICE_OUT) / 1e6


# --- What the learner read --------------------------------------------------------------


def start(
    conn: sqlite3.Connection, content_id: int, chosen_via: str, session_id: int | None = None
) -> None:
    """Log that the learner started an item: 'recommended' (the default) or 'requested'.

    An explicit request may be any item, any chapter: the learner's choice always wins.
    """
    with conn:
        conn.execute(
            "INSERT INTO content_events (content_id, event, chosen_via, session_id) "
            "VALUES (?, 'started', ?, ?)",
            (content_id, chosen_via, session_id),
        )


def finish(conn: sqlite3.Connection, content_id: int, session_id: int | None = None) -> None:
    with conn:
        conn.execute(
            "INSERT INTO content_events (content_id, event, session_id) VALUES (?, 'finished', ?)",
            (content_id, session_id),
        )


# --- Command line -----------------------------------------------------------------------


def analysis_fingerprint() -> str:
    from spanish_tutor.ingest.tatoeba import analysis_setup

    return json.dumps(analysis_setup(), sort_keys=True)


def write_review(path: Path, rows: list[tuple[int, Outcome]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        out = csv.writer(f)
        out.writerow(
            [
                "content_id",
                "form",
                "tagged",
                "occurrences",
                "verdict",
                "lemma",
                "pos",
                "reason",
                "context",
            ]
        )
        for content_id, o in rows:
            p = o.pending
            out.writerow(
                [
                    content_id,
                    p.form,
                    f"{p.lemma}|{p.pos}",
                    p.occurrences,
                    o.verdict,
                    o.lemma,
                    o.pos,
                    o.reason,
                    p.context,
                ]
            )


def cmd_index(conn: sqlite3.Connection, ids: list[int], resolve: bool, yes: bool) -> None:
    from spanish_tutor import lexicon
    from spanish_tutor.ingest.expressions import definitions
    from spanish_tutor.resolve import ClaudeResolver

    corrector = lexicon.load_corrector()
    index = LexiconIndex(conn, lexicon.load_wiktionary(), definitions())
    fingerprint = analysis_fingerprint()

    def analyze_many(units: list[str]) -> Iterable[list[TokenAnalysis]]:
        return lexicon.analyze(units, corrector=corrector)

    resolver = ClaudeResolver() if resolve else None
    review: list[tuple[int, Outcome]] = []
    for content_id in ids:
        kind, text = item_kind(conn, content_id)
        analyzed = analyze_text(text, kind, analyze_many)
        if resolver is not None:
            # Look first: show what the call would cost, and ask, before spending anything.
            plan = plan_index(conn, index, analyzed)
            if plan.pending:
                cost = estimate_cost(len(plan.pending))
                print(f"item {content_id}: {len(plan.pending)} forms to resolve, ~${cost:.2f}")
                if not yes and input("  call the model? [y/N] ").strip().lower() != "y":
                    print("  skipped (not indexed)")
                    continue
        result = index_item(
            conn,
            index,
            content_id,
            analyze_many,
            fingerprint,
            resolver=resolver,
            reviewer=resolver.reviewer if resolver else None,
            analyzed=analyzed,
        )
        review += [(content_id, o) for o in result.outcomes]
        verdicts = Counter(o.verdict for o in result.outcomes)
        cost = ""
        if result.asked:
            dollars = (
                result.asked.input_tokens * PRICE_IN + result.asked.output_tokens * PRICE_OUT
            ) / 1e6
            cost = (
                f"; model: {result.asked.input_tokens:,} in / {result.asked.output_tokens:,} out "
                f"tokens, ${dollars:.3f}"
            )
        print(
            f"item {content_id}: {result.words:,} words, {result.tokens:,} tokens, "
            f"{result.unresolved_tokens:,} unresolved; {result.from_dictionary} added from "
            f"dictionaries, {result.from_cache} from earlier decisions; "
            f"{dict(verdicts) or 'nothing to resolve'}{cost}"
        )
    if review:
        write_review(RESOLUTIONS_CSV, review)
        print(f"review file: {RESOLUTIONS_CSV}")


def cmd_list(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT c.content_id, c.kind, c.title, b.title AS book, c.chapter_no, c.tokens,
               c.unresolved_tokens, c.indexed_at
        FROM content_items AS c
        LEFT JOIN books AS b USING (book_id)
        ORDER BY c.content_id
        """
    )
    for r in rows:
        where = f" ({r['book']}, ch. {r['chapter_no']})" if r["book"] else ""
        state = (
            f"{r['tokens']:,} tokens, {r['unresolved_tokens']:,} unresolved"
            if r["indexed_at"]
            else "not indexed"
        )
        print(f"{r['content_id']:>5}  {r['kind']:<7} {r['title']}{where}: {state}")


def chapter_title(path: Path) -> str:
    """A chapter file's title: its name without the number that orders it ("03 El solitario")."""
    return re.sub(r"^\d+[\s._-]*", "", path.stem) or path.stem


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Add and index songs, stories and books.")
    commands = parser.add_subparsers(dest="command", required=True)
    for kind in ("song", "story"):
        p = commands.add_parser(f"add-{kind}", help=f"add a {kind} from a UTF-8 text file")
        p.add_argument("file", type=Path)
        p.add_argument("--title", required=True)
        p.add_argument("--author")
        p.add_argument(
            "--source",
            default="private" if kind == "song" else None,
            required=kind != "song",
            help="e.g. gutenberg:12345",
        )
        p.add_argument(
            "--private",
            action="store_true",
            default=kind == "song",
            help="copyrighted: local use only (songs always are)",
        )
    p = commands.add_parser("add-book", help="add a book: one .txt file per chapter, in order")
    p.add_argument("folder", type=Path, help="chapter files, sorted by name = reading order")
    p.add_argument("--title", required=True)
    p.add_argument("--author")
    p.add_argument("--source", required=True)
    p.add_argument("--private", action="store_true")
    p = commands.add_parser("index", help="index items (default: every stale item)")
    p.add_argument("ids", nargs="*", type=int)
    p.add_argument(
        "--no-resolve",
        action="store_true",
        help="don't call the model: unknown forms count as unresolved",
    )
    p.add_argument("--yes", action="store_true", help="don't ask before each model call")
    commands.add_parser("list", help="list content items")
    args = parser.parse_args()

    conn = db.connect()
    if pending := db.pending_migrations(conn):
        print(f"Upgrading the database (migrations {pending}); backup: {db.backup()}")
    db.init_schema(conn)

    if args.command in ("add-song", "add-story"):
        kind = args.command.removeprefix("add-")
        is_private = args.private or kind == "song"
        with conn:
            content_id = add_item(
                conn,
                kind,
                args.title,
                read_text(args.file),
                source=args.source,
                is_private=is_private,
                author=args.author,
            )
        print(f"added {kind} {content_id}: {args.title} (run `index` next)")
    elif args.command == "add-book":
        files = sorted(args.folder.glob("*.txt"))
        if not files:
            sys.exit(f"no .txt chapter files in {args.folder}")
        chapters = [(chapter_title(f), read_text(f)) for f in files]
        with conn:
            book_id, ids = add_book(
                conn,
                args.title,
                chapters,
                source=args.source,
                is_private=args.private,
                author=args.author,
            )
        print(f"added book {book_id}: {args.title}, chapters {ids[0]}-{ids[-1]}")
    elif args.command == "index":
        ids = args.ids or stale_items(conn, analysis_fingerprint())
        if not ids:
            print("nothing to index")
            return
        cmd_index(conn, ids, resolve=not args.no_resolve, yes=args.yes)
    else:
        cmd_list(conn)


if __name__ == "__main__":
    main()
