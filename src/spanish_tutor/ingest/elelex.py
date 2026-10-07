"""Tag lexemes with CEFR levels from ELELex, for the vocabulary-readiness rings.

    uv run python -m spanish_tutor.ingest.elelex [--min-docs 3]

ELELex (CEFRLex project, CENTAL, UCLouvain; CC BY-NC-SA 4.0,
https://cental.uclouvain.be/cefrlex/elelex/) counts how often 14,290 Spanish words occur
in the reading texts of textbooks and graded readers at each level, A1 to C1 (no C2).
A word's level here is the first level at which at least --min-docs documents use it
(sql/fill_cefr_levels.sql). It describes receptive, textbook vocabulary: the rings it
feeds are vocabulary readiness, never a CEFR level.

The Plan Curricular del Instituto Cervantes was the first idea, but it is "Reservados
todos los derechos", so it would need permission; ELELex is openly licensed like
SUBTLEX-ESP. The file is downloaded to data/raw/ (gitignored) and checked by SHA-256.

ELELex was lemmatized by FreeLing, which follows other conventions than our lexicon
(lexicon.py), so entries are normalized before matching:
  * FreeLing tags become our POS; names (NP) and punctuation, dates and units are dropped.
  * Multi-word entries (a_el_aire_libre) become expressions written as ours are
    (al aire libre, pos EXPR).
  * FreeLing files a feminine noun under its masculine lemma, tagged NCF (niña = niño
    NCF). When a lemma is listed as both masculine and feminine noun, its NCF entry is
    the feminine word: -o becomes -a (esposa), -or becomes -ora (señora). A noun listed
    only as feminine (mano NCF) is a feminine word in its own right and is kept as is.
  * Function words also match on their lemma under any POS (in the SQL).
Words that still don't match (numbers, most -mente adverbs, ella, ustedes) are left
without a level; the rings count only words that have one.
"""

import argparse
import csv
import hashlib
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import NamedTuple

from spanish_tutor import db
from spanish_tutor.config import DB_PATH, SQL_DIR
from spanish_tutor.ingest.download import RAW_DIR, fetch

URL = "https://cental.uclouvain.be/cefrlex/static/resources/es/ELELex.tsv"
SHA256 = "87a28dc6d3c5c2344883698f7bc77e259bd42212446ac2b52761a3fc8f5f26cf"  # fetched 2026-10-07
PATH = RAW_DIR / "ELELex.tsv"

LEVELS = ("A1", "A2", "B1", "B2", "C1")
MIN_DOCS = 3

# FreeLing (EAGLES) tag, by its first letter -> our POS. NP (names) is handled separately.
POS_BY_TAG = {
    "N": "NOUN",
    "A": "ADJ",
    "V": "VERB",
    "R": "ADV",
    "I": "INTJ",
    "S": "ADP",
    "D": "DET",
    "P": "PRON",
}
CONJUNCTIONS = {"CC": "CCONJ", "CS": "SCONJ"}

STAGING_TABLE = """
CREATE TEMP TABLE elelex_staging (
    lemma TEXT NOT NULL, pos TEXT NOT NULL,
    docs_a1 REAL NOT NULL, docs_a2 REAL NOT NULL, docs_b1 REAL NOT NULL,
    docs_b2 REAL NOT NULL, docs_c1 REAL NOT NULL
)
"""


class Entry(NamedTuple):
    lemma: str
    tag: str  # FreeLing's, e.g. NCF
    docs: tuple[float, ...]  # documents using the word, per level A1..C1


class Staged(NamedTuple):
    lemma: str
    pos: str
    docs: tuple[float, ...]

    def row(self) -> tuple:
        return (self.lemma, self.pos, *self.docs)


def download(path: Path = PATH) -> None:
    """Fetch ELELex once and check it is the file the levels were measured on."""
    fetch(URL, path)
    with open(path, "rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    if digest != SHA256:
        sys.exit(
            f"{path.name}: SHA-256 {digest} doesn't match the pinned {SHA256}. ELELex has "
            "changed; re-measure the levels before updating the pin."
        )


def read(path: Path = PATH) -> Iterator[Entry]:
    """The TSV's rows: word, tag, five level frequencies, a total, five document counts."""
    with open(path, encoding="utf-8", newline="") as file:
        reader = csv.reader(file, delimiter="\t")
        header = next(reader)
        docs_at = [header.index(f"nb_doc@{level.lower()}") for level in LEVELS]
        for row in reader:
            yield Entry(row[0], row[1], tuple(float(row[i]) for i in docs_at))


def pos_of(tag: str) -> str | None:
    """Our POS for a FreeLing tag; None for names and tags we don't use."""
    tag = tag.split(",")[0].strip()  # a few rows list two tags ("NCM, NP0")
    if not tag or tag.startswith("NP"):
        return None
    if tag[0] == "C":
        return CONJUNCTIONS.get(tag)
    return POS_BY_TAG.get(tag[0])


def normalize(word: str) -> str:
    """Written as lexicon.py writes lemmas: NFC, lowercase; multi-word entries spaced and
    with the contractions al and del, as our expressions are."""
    text = " ".join(unicodedata.normalize("NFC", word).lower().replace("_", " ").split())
    words = text.split(" ")
    out: list[str] = []
    for w in words:
        if w == "el" and out and out[-1] in ("a", "de"):
            out[-1] += "l"
        else:
            out.append(w)
    return " ".join(out)


def feminine(lemma: str) -> str | None:
    """The feminine noun FreeLing files under a masculine lemma: niño -> niña,
    señor -> señora. None when there's no regular feminine to build."""
    if lemma.endswith("o"):
        return lemma[:-1] + "a"
    if lemma.endswith("or"):
        return lemma + "a"
    return None


def stage(entries: Iterable[Entry]) -> list[Staged]:
    """Normalize ELELex entries into (lemma, pos, docs) rows for fill_cefr_levels.sql."""
    entries = list(entries)
    noun_tags: dict[str, set[str]] = defaultdict(set)
    for entry in entries:
        if pos_of(entry.tag) == "NOUN":
            noun_tags[normalize(entry.lemma)].add(entry.tag.split(",")[0].strip()[:3])
    staged = []
    for entry in entries:
        pos = pos_of(entry.tag)
        if pos is None:
            continue
        lemma = normalize(entry.lemma)
        if " " in lemma:
            staged.append(Staged(lemma, "EXPR", entry.docs))
            continue
        tag = entry.tag.split(",")[0].strip()
        if tag.startswith("NCF") and "NCM" in noun_tags[lemma]:
            # A gender pair: this entry is the feminine word. Without a regular
            # feminine form (an -e noun such as estudiante) it is the same word.
            lemma = feminine(lemma) or lemma
        staged.append(Staged(lemma, pos, entry.docs))
    return staged


class FillCounts(NamedTuple):
    staged: int
    exact: int  # lexemes tagged by an exact (lemma, pos) match
    function_words: int  # tagged by the any-POS lemma match


def fill_levels(
    conn: sqlite3.Connection, staged: Iterable[Staged], min_docs: int = MIN_DOCS
) -> FillCounts:
    """Stage the rows and set lexemes.cefr_level with sql/fill_cefr_levels.sql, in one
    transaction (statements run one at a time so each can report its count)."""
    params = {"min_docs": min_docs}
    with conn:
        conn.execute("DROP TABLE IF EXISTS temp.elelex_staging")
        conn.execute(STAGING_TABLE)
        conn.executemany(
            "INSERT INTO elelex_staging VALUES (?, ?, ?, ?, ?, ?, ?)", (s.row() for s in staged)
        )
        count = conn.execute("SELECT COUNT(*) FROM elelex_staging").fetchone()[0]
        clear, exact, function_words = db.statements(
            (SQL_DIR / "fill_cefr_levels.sql").read_text(encoding="utf-8")
        )
        conn.execute(clear)
        # rowcount is -1 for a statement that starts with WITH, so count changes instead.
        before = conn.total_changes
        conn.execute(exact, params)
        tagged = conn.total_changes - before
        conn.execute(function_words, params)
        fallback = conn.total_changes - before - tagged
        conn.execute("DROP TABLE elelex_staging")
    return FillCounts(count, tagged, fallback)


def apply(conn: sqlite3.Connection, path: Path = PATH, min_docs: int = MIN_DOCS) -> FillCounts:
    return fill_levels(conn, stage(read(path)), min_docs)


def level_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return dict(
        conn.execute(
            "SELECT cefr_level, COUNT(*) FROM lexemes WHERE cefr_level IS NOT NULL "
            "GROUP BY cefr_level ORDER BY cefr_level"
        ).fetchall()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--min-docs", type=int, default=MIN_DOCS)
    args = parser.parse_args()
    download()
    if backup := db.backup(DB_PATH):
        print(f"backed up the database to {backup.name}")
    conn = db.connect(DB_PATH)
    try:
        db.init_schema(conn)
        counts = apply(conn, min_docs=args.min_docs)
        print(
            f"ELELex: {counts.staged:,} entries staged; {counts.exact:,} lexemes tagged by "
            f"(lemma, pos), {counts.function_words:,} function words by lemma"
        )
        for level, words in level_counts(conn).items():
            print(f"  {level}: {words:,}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
