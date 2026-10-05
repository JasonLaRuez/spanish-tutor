"""Tatoeba Spanish sentences with English translations (CC BY 2.0 FR).

    uv run python -m spanish_tutor.ingest.tatoeba
        Lemmatize every Spanish sentence once and cache the result (about an hour on CPU).
        Re-analyzes automatically when the cache was made by a different tagger.

Tatoeba requires crediting each sentence's author, so the author travels with every
sentence. Export files are tab-separated with no header row and `\\N` for null, and
sentences may contain quote characters, so they're parsed with QUOTE_NONE.
"""

import bz2
import csv
import json
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from spanish_tutor.config import DATA_DIR
from spanish_tutor.lexicon import LemmaCorrector, TokenAnalysis, analyze, load_corrector, tagger

NULL = "\\N"
RAW_DIR = DATA_DIR / "raw"
ANALYZED_PATH = DATA_DIR / "processed" / "tatoeba_analyzed.jsonl"
CORRECTIONS_PATH = DATA_DIR / "processed" / "lemma_corrections.csv"
RESTORATIONS_PATH = DATA_DIR / "processed" / "accent_restorations.csv"


@dataclass(frozen=True)
class Sentence:
    id: int
    es: str
    en: str | None
    author: str | None


@dataclass(frozen=True)
class AnalyzedSentence(Sentence):
    tokens: list[TokenAnalysis]


def read_tsv(path: Path) -> Iterator[list[str]]:
    opener = bz2.open if path.suffix == ".bz2" else open
    with opener(path, "rt", encoding="utf-8", newline="") as f:
        yield from csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)


def load_sentences(spa_detailed: Path, eng_sentences: Path, links: Path) -> list[Sentence]:
    """Spanish sentences, each with its lowest-id English translation (if any)."""
    spa = [
        (int(row[0]), row[2], None if row[3] == NULL else row[3])
        for row in read_tsv(spa_detailed)
        if row[1] == "spa"
    ]
    translation_id: dict[int, int] = {}
    for spa_id, eng_id in ((int(a), int(b)) for a, b in read_tsv(links)):
        if eng_id < translation_id.get(spa_id, eng_id + 1):
            translation_id[spa_id] = eng_id
    wanted = set(translation_id.values())
    english = {int(row[0]): row[2] for row in read_tsv(eng_sentences) if int(row[0]) in wanted}
    return [
        Sentence(sid, text, english.get(translation_id.get(sid, -1)), author)
        for sid, text, author in spa
    ]


class TaggerMismatch(Exception):
    """The cached corpus was analyzed with a different setup than the one in use."""


def meta_path(analyzed: Path) -> Path:
    """Where the setup that made `analyzed` is recorded: tatoeba_analyzed.meta.json."""
    return analyzed.with_name(analyzed.stem + ".meta.json")


def analysis_setup() -> dict:
    """What an analysis depends on besides the code: the tagger and the expression list."""
    from spanish_tutor.ingest.expressions import signature

    return {"tagger": tagger(), "expressions": signature()}


def record_tagger(analyzed: Path) -> None:
    meta_path(analyzed).write_text(json.dumps(analysis_setup()), encoding="utf-8")


def analyzed_by(analyzed: Path) -> dict | None:
    """The setup recorded for a cached corpus; None if unrecorded (made before 2026-10-02).

    A record from before expressions existed has no "expressions" key: read as None.
    """
    try:
        recorded = json.loads(meta_path(analyzed).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    return {"tagger": recorded.get("tagger"), "expressions": recorded.get("expressions")}


def require_current(analyzed: Path = ANALYZED_PATH) -> None:
    """Refuse a cached corpus made with another tagger or another expression list.

    Its (lemma, pos) pairs would silently disagree with how live text (a learner's turn)
    is analyzed, so a word could count as both known and new.
    """
    found, expected = analyzed_by(analyzed), analysis_setup()
    if found != expected:
        raise TaggerMismatch(
            f"{analyzed.name} was analyzed with {found or 'an unrecorded setup'}, not "
            f"{expected}. Re-analyze it: `uv run python -m spanish_tutor.ingest.tatoeba` "
            "(about 90 minutes on CPU)."
        )


def write_analyzed(sentences: list[Sentence], dest: Path) -> None:
    """Run the shared lemmatizer over every sentence once and cache the result as JSONL.

    The tagger is recorded next to it (see `require_current`).
    """
    partial = dest.with_suffix(dest.suffix + ".part")
    with partial.open("w", encoding="utf-8") as out:
        analyses = analyze(s.es for s in sentences)
        for n, (sentence, tokens) in enumerate(zip(sentences, analyses, strict=True), 1):
            record = asdict(sentence) | {"tokens": tokens}
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            if n % 50_000 == 0:
                print(f"  {n:,} / {len(sentences):,} sentences analyzed", flush=True)
    meta_path(dest).unlink(missing_ok=True)
    partial.replace(dest)
    record_tagger(dest)


def read_analyzed(path: Path) -> Iterator[AnalyzedSentence]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            record["tokens"] = [
                (form, [tuple(a) for a in analyses]) for form, analyses in record["tokens"]
            ]
            yield AnalyzedSentence(**record)


def main() -> None:
    if ANALYZED_PATH.exists():
        if (found := analyzed_by(ANALYZED_PATH)) == analysis_setup():
            print(f"skip  {ANALYZED_PATH.name} (exists; delete it to re-analyze)")
            return
        print(f"re-analyzing: {ANALYZED_PATH.name} was made with {found or 'an unrecorded setup'}")
    print("loading Tatoeba sentences ...")
    sentences = load_sentences(
        RAW_DIR / "spa_sentences_detailed.tsv.bz2",
        RAW_DIR / "eng_sentences.tsv.bz2",
        RAW_DIR / "spa-eng_links.tsv.bz2",
    )
    translated = sum(1 for s in sentences if s.en)
    print(f"  {len(sentences):,} Spanish sentences, {translated:,} with English translations")
    ANALYZED_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_analyzed(sentences, ANALYZED_PATH)
    print(f"wrote {ANALYZED_PATH}")
    report_corrections(load_corrector(), CORRECTIONS_PATH)
    report_restorations(load_corrector(), RESTORATIONS_PATH)


def report_corrections(corrector: LemmaCorrector, dest: Path) -> None:
    """Write every lemma correction (and unresolved case) with its count, for review."""
    corrected, unresolved = corrector.corrected, corrector.unresolved
    with dest.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["form", "tagger_lemma", "tagger_pos", "lemma", "pos", "tokens"])
        for (form, before, after), n in corrected.most_common():
            writer.writerow([form, *before, *after, n])
        for (form, before), n in unresolved.most_common():
            writer.writerow([form, *before, "", "", n])
    print(
        f"lemma corrections: {sum(corrected.values()):,} tokens ({len(corrected):,} distinct); "
        f"unresolved: {sum(unresolved.values()):,} tokens. Details in {dest.name}"
    )
    for (form, before, after), n in corrected.most_common(15):
        print(f"  {n:>6,}  {form:12} {before[0]}|{before[1]} -> {after[0]}|{after[1]}")


def report_restorations(corrector: LemmaCorrector, dest: Path) -> None:
    """Write every accent restoration (as typed -> restored) with its count, for review."""
    if corrector.restorer is None:
        return
    restored = corrector.restorer.restored
    with dest.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["typed", "restored", "tokens"])
        for (typed, word), n in restored.most_common():
            writer.writerow([typed, word, n])
    print(
        f"accent restorations: {sum(restored.values()):,} tokens ({len(restored):,} distinct). "
        f"Details in {dest.name}"
    )
    for (typed, word), n in restored.most_common(15):
        print(f"  {n:>6,}  {typed} -> {word}")


if __name__ == "__main__":
    main()
