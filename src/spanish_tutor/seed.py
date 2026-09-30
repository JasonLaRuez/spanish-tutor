"""Seed the word bank with vocabulary the learner already knows.

    uv run python -m spanish_tutor.seed candidates [--top 1500]
        Rank (lemma, pos) pairs by frequency and write data/processed/seed_candidates.csv.
        Mark its `known` column: r = recognize, p = can produce (implies recognize).
    uv run python -m spanish_tutor.seed build
        Turn the marked rows into data/processed/seed_words.sql and load it.

Frequency comes from SUBTLEX-ESP, which lists word forms only. Each form's count is split
across the (lemma, pos) analyses spaCy gave that form in real Tatoeba sentences, so
ambiguous forms ("bajo", "como") are divided by how they're actually used. Only pairs with
a Wiktionary entry are kept, which filters out names, English words, and tagger errors.

All outputs derive from CC BY-NC-SA / CC BY-SA / CC BY sources and hold the learner's
personal word list, so they live in data/processed/ (gitignored) and are never committed.
"""

import argparse
import csv
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from spanish_tutor import db
from spanish_tutor.config import DATA_DIR, DB_PATH
from spanish_tutor.ingest import subtlex
from spanish_tutor.ingest.download import RAW_DIR, WIKTIONARY_FILE
from spanish_tutor.ingest.tatoeba import ANALYZED_PATH, AnalyzedSentence, read_analyzed
from spanish_tutor.ingest.wiktionary import Wiktionary
from spanish_tutor.lexicon import Analysis

PROCESSED_DIR = DATA_DIR / "processed"
CANDIDATES_CSV = PROCESSED_DIR / "seed_candidates.csv"
REJECTS_CSV = PROCESSED_DIR / "seed_rejects.csv"
SEED_SQL = PROCESSED_DIR / "seed_words.sql"

MARKS = {"r": "recognize", "p": "produce"}

# Example sentence length, in word tokens.
MIN_LENGTH, IDEAL_LENGTH, MAX_LENGTH = 4, 6, 10


# --- Frequency -------------------------------------------------------------------------


def form_analysis_counts(
    sentences: Iterable[AnalyzedSentence],
) -> tuple[Counter[str], dict[str, Counter[Analysis]]]:
    """How often each surface form occurs, and how often with each (lemma, pos)."""
    occurrences: Counter[str] = Counter()
    with_analysis: dict[str, Counter[Analysis]] = defaultdict(Counter)
    for sentence in sentences:
        for form, analyses in sentence.tokens:
            occurrences[form] += 1
            with_analysis[form].update(analyses)
    return occurrences, with_analysis


def lemma_frequencies(
    form_counts: Counter[str],
    occurrences: Counter[str],
    with_analysis: dict[str, Counter[Analysis]],
) -> tuple[Counter[Analysis], int]:
    """Split each form's subtitle count across its analyses, weighted by Tatoeba usage.

    A contraction's analyses each get the full count ("del" is always both "de" and "el").
    Forms that never occur in Tatoeba can't be disambiguated; their count is returned as
    the second value so the caller can report how much frequency mass was dropped.
    """
    frequencies: Counter[Analysis] = Counter()
    dropped = 0
    for form, count in form_counts.items():
        seen = occurrences.get(form, 0)
        if not seen:
            dropped += count
            continue
        for analysis, n in with_analysis[form].items():
            frequencies[analysis] += count * n / seen
    return frequencies, dropped


# --- Examples --------------------------------------------------------------------------


@dataclass(frozen=True)
class Example:
    sentence_id: int
    es: str
    en: str
    author: str | None


def pick_examples(
    sentences: Iterable[AnalyzedSentence], wanted: set[Analysis], known: set[Analysis]
) -> dict[Analysis, Example]:
    """Best translated example sentence for each wanted (lemma, pos).

    Preference: fewest *other* words outside `known` (comprehensible input: ideally a
    sentence the learner can fully read), then a useful length (fragments like "Soy."
    show no usage; long sentences bury the word), then closest to IDEAL_LENGTH words,
    then lowest id.
    """
    best: dict[Analysis, tuple[tuple[int, int, int, int], Example]] = {}
    for sentence in sentences:
        if not sentence.en:
            continue
        vocab = {a for _, analyses in sentence.tokens for a in analyses}
        present = vocab & wanted
        if not present:
            continue
        unknown = vocab - known
        length = len(sentence.tokens)
        outside_range = 0 if MIN_LENGTH <= length <= MAX_LENGTH else 1
        for analysis in present:
            key = (
                len(unknown - {analysis}),
                outside_range,
                abs(length - IDEAL_LENGTH),
                sentence.id,
            )
            if analysis not in best or key < best[analysis][0]:
                example = Example(sentence.id, sentence.es, sentence.en, sentence.author)
                best[analysis] = (key, example)
    return {analysis: example for analysis, (_, example) in best.items()}


# --- Seed SQL --------------------------------------------------------------------------


@dataclass(frozen=True)
class SeedWord:
    lemma: str
    pos: str
    can_produce: bool
    definition_en: str | None
    example: Example | None


def sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def seed_sql(words: list[SeedWord]) -> str:
    """A re-runnable SQL script that adds the seed words and their 'seed' events."""
    rows = []
    for w in words:
        ex = w.example
        values = [
            w.lemma,
            w.pos,
            w.can_produce,
            w.definition_en,
            "wiktionary" if w.definition_en else None,
            ex.es if ex else None,
            ex.en if ex else None,
            f"tatoeba:{ex.sentence_id}" if ex else None,
            ex.author if ex else None,
        ]
        rows.append("    (" + ", ".join(sql_literal(v) for v in values) + ")")
    return f"""\
-- Generated by `python -m spanish_tutor.seed build` on {datetime.now(UTC).date().isoformat()}.
-- Derived from SUBTLEX-ESP, Wiktionary and Tatoeba data. Local only: do not commit.
-- Safe to re-run: existing lexemes keep their data, and seed events are added only once.
BEGIN;

CREATE TEMP TABLE seed (
    lemma             TEXT NOT NULL,
    pos               TEXT NOT NULL,
    can_produce       INTEGER NOT NULL,
    definition_en     TEXT,
    definition_source TEXT,
    example_es        TEXT,
    example_en        TEXT,
    example_source    TEXT,
    example_author    TEXT
);

INSERT INTO seed VALUES
{",\n".join(rows)};

-- New words are inserted; for words already present (e.g. found earlier in content),
-- only fields that are still empty get filled in.
INSERT INTO lexemes
    (lemma, pos, definition_en, definition_source,
     example_es, example_en, example_source, example_author)
SELECT lemma, pos, definition_en, definition_source,
       example_es, example_en, example_source, example_author
FROM seed
WHERE true  -- SQLite needs a WHERE here to parse ON CONFLICT after a SELECT
ON CONFLICT (lemma, pos) DO UPDATE SET
    definition_en     = COALESCE(lexemes.definition_en, excluded.definition_en),
    definition_source = COALESCE(lexemes.definition_source, excluded.definition_source),
    example_es        = COALESCE(lexemes.example_es, excluded.example_es),
    example_en        = COALESCE(lexemes.example_en, excluded.example_en),
    example_source    = COALESCE(lexemes.example_source, excluded.example_source),
    example_author    = COALESCE(lexemes.example_author, excluded.example_author);

-- Every seed word is recognized ...
INSERT INTO word_events (lexeme_id, mode, event_type, source)
SELECT l.lexeme_id, 'recognition', 'taught', 'seed'
FROM seed AS s
JOIN lexemes AS l ON l.lemma = s.lemma AND l.pos = s.pos
WHERE NOT EXISTS (
    SELECT 1 FROM word_events AS e
    WHERE e.lexeme_id = l.lexeme_id AND e.mode = 'recognition' AND e.source = 'seed'
);

-- ... and words marked 'p' can also be produced.
INSERT INTO word_events (lexeme_id, mode, event_type, source)
SELECT l.lexeme_id, 'production', 'used', 'seed'
FROM seed AS s
JOIN lexemes AS l ON l.lemma = s.lemma AND l.pos = s.pos
WHERE s.can_produce = 1
  AND NOT EXISTS (
    SELECT 1 FROM word_events AS e
    WHERE e.lexeme_id = l.lexeme_id AND e.mode = 'production' AND e.source = 'seed'
);

DROP TABLE seed;
COMMIT;
"""


# --- Commands --------------------------------------------------------------------------


def require(*paths: Path) -> None:
    missing = [p for p in paths if not p.exists()]
    if missing:
        names = ", ".join(p.name for p in missing)
        sys.exit(
            f"Missing {names}. Run `uv run python -m spanish_tutor.ingest.download` and "
            "`uv run python -m spanish_tutor.ingest.tatoeba` first."
        )


def cmd_candidates(top: int) -> None:
    subtlex_path = RAW_DIR / "SUBTLEX-ESP.xlsx"
    wiktionary_path = RAW_DIR / WIKTIONARY_FILE
    require(subtlex_path, wiktionary_path, ANALYZED_PATH)

    print("counting forms ...")
    form_counts = subtlex.load_counts(subtlex_path)
    occurrences, with_analysis = form_analysis_counts(read_analyzed(ANALYZED_PATH))
    frequencies, dropped = lemma_frequencies(form_counts, occurrences, with_analysis)
    total = sum(form_counts.values())
    print(f"  {dropped / total:.1%} of subtitle tokens are forms absent from Tatoeba (dropped)")

    print("checking against Wiktionary ...")
    wiktionary = Wiktionary(wiktionary_path)
    ranked = frequencies.most_common()
    accepted = [(a, f) for a, f in ranked if a in wiktionary][:top]
    cutoff = accepted[-1][1] if accepted else 0
    rejected = [(a, f) for a, f in ranked if f >= cutoff and a not in wiktionary]

    print("choosing examples ...")
    wanted = {a for a, _ in accepted}
    examples = pick_examples(read_analyzed(ANALYZED_PATH), wanted, known=wanted)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: Excel only detects UTF-8 (and shows accents correctly) with a BOM.
    with CANDIDATES_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "rank",
                "lemma",
                "pos",
                "definition_en",
                "example_es",
                "example_en",
                "known",
            ]
        )
        for rank, ((lemma, pos), _) in enumerate(accepted, 1):
            ex = examples.get((lemma, pos))
            writer.writerow(
                [rank, lemma, pos, wiktionary.definition(lemma, pos),
                 ex.es if ex else "", ex.en if ex else "", ""]
            )  # fmt: skip
    with REJECTS_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["lemma", "pos", "estimated_count"])
        writer.writerows([lemma, pos, round(freq)] for (lemma, pos), freq in rejected)

    print(f"wrote {len(accepted)} candidates to {CANDIDATES_CSV}")
    print(f"wrote {len(rejected)} rejected pairs (no Wiktionary entry) to {REJECTS_CSV}")
    print("Mark the `known` column: r = recognize, p = can produce. Then run `seed build`.")


def read_marks(path: Path) -> dict[Analysis, bool]:
    """(lemma, pos) -> can_produce, for every marked row. Unrecognized marks are errors."""
    marks: dict[Analysis, bool] = {}
    errors = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for line, row in enumerate(csv.DictReader(f), start=2):
            mark = (row.get("known") or "").strip().lower()
            if not mark:
                continue
            if mark not in MARKS:
                errors.append(f"  line {line}: {row['lemma']!r} has mark {mark!r}")
                continue
            marks[row["lemma"], row["pos"]] = mark == "p"
    if errors:
        sys.exit("Marks must be r, p, or blank:\n" + "\n".join(errors))
    return marks


def cmd_build() -> None:
    wiktionary_path = RAW_DIR / WIKTIONARY_FILE
    require(CANDIDATES_CSV, wiktionary_path, ANALYZED_PATH)

    marks = read_marks(CANDIDATES_CSV)
    if not marks:
        sys.exit(f"No rows are marked in {CANDIDATES_CSV}.")
    known = set(marks)
    examples = pick_examples(read_analyzed(ANALYZED_PATH), known, known)
    wiktionary = Wiktionary(wiktionary_path)
    words = [
        SeedWord(lemma, pos, can_produce, wiktionary.definition(lemma, pos), examples.get((lemma, pos)))
        for (lemma, pos), can_produce in marks.items()
    ]  # fmt: skip

    SEED_SQL.write_text(seed_sql(words), encoding="utf-8")
    print(f"wrote {SEED_SQL}")

    conn = db.connect(DB_PATH)
    try:
        db.init_schema(conn)
        conn.executescript(SEED_SQL.read_text(encoding="utf-8"))
        counts = conn.execute("SELECT mode, COUNT(*) FROM word_bank GROUP BY mode ORDER BY mode")
        print(f"loaded into {DB_PATH}:")
        for mode, n in counts:
            print(f"  {mode:12s} {n:,} words")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the word bank.")
    commands = parser.add_subparsers(dest="command", required=True)
    candidates = commands.add_parser("candidates", help="write the candidate list to mark")
    candidates.add_argument("--top", type=int, default=1500)
    commands.add_parser("build", help="load the marked candidates into the word bank")
    args = parser.parse_args()

    if args.command == "candidates":
        cmd_candidates(args.top)
    else:
        cmd_build()


if __name__ == "__main__":
    main()
