"""Fetch raw corpora into data/raw/ (gitignored). Existing files are skipped.

    uv run python -m spanish_tutor.ingest.download [--skip-wiktionary]

The Wiktionary step reads kaikki.org's Spanish extract (~95 MB compressed transfer) and
keeps a compact subset. If that file is gone (kaikki has deprecated it), it falls back to
streaming the all-languages extract (~3 GB gzipped, about an hour) and filtering it to
Spanish on the fly; neither full file is written to disk.
"""

import argparse
import gzip
import json
import shutil
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from spanish_tutor.config import DATA_DIR

RAW_DIR = DATA_DIR / "raw"

TATOEBA = "https://downloads.tatoeba.org/exports/per_language"
FILES = {
    # SUBTLEX-ESP, CC BY-NC-SA 4.0: https://osf.io/xp6sz/
    "SUBTLEX-ESP.xlsx": "https://osf.io/download/fxt57/",
    # Tatoeba, CC BY 2.0 FR: https://tatoeba.org/en/downloads
    "spa_sentences_detailed.tsv.bz2": f"{TATOEBA}/spa/spa_sentences_detailed.tsv.bz2",
    "eng_sentences.tsv.bz2": f"{TATOEBA}/eng/eng_sentences.tsv.bz2",
    "spa-eng_links.tsv.bz2": f"{TATOEBA}/spa/spa-eng_links.tsv.bz2",
}

# Wiktionary via kaikki.org (wiktextract), CC BY-SA: https://kaikki.org/dictionary/rawdata.html
KAIKKI_SPANISH_URL = "https://kaikki.org/dictionary/Spanish/kaikki.org-dictionary-Spanish.jsonl"
KAIKKI_ALL_URL = "https://kaikki.org/dictionary/raw-wiktextract-data.jsonl.gz"
WIKTIONARY_FILE = "wiktionary_es.jsonl"
MAX_SENSES = 5


def fetch(url: str, dest: Path) -> None:
    if dest.exists():
        print(f"skip  {dest.name} (exists)")
        return
    print(f"fetch {dest.name}")
    partial = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out)
    partial.replace(dest)


# Function-word classes. Wiktionary records many of their words as "forms" of another
# (ti = prepositional form of tú, lo = accusative of él), yet a learner has to learn
# each one separately, so their form-of senses are kept.
CLOSED_CLASS_POS = frozenset({"pron", "det", "article", "prep", "conj", "num", "particle"})


def compact_entry(entry: dict) -> dict | None:
    """Reduce a wiktextract entry to what we use; None if it has no usable senses.

    Inflected forms of open-class words ("pies" -> form of "pie", "crees" -> form of
    "creer") are their own entries in wiktextract; their senses are dropped so only
    dictionary forms remain.
    """
    pos = entry.get("pos", "")
    senses = []
    for sense in entry.get("senses", []):
        tags = sense.get("tags", [])
        glosses = sense.get("glosses")
        is_form_of = "form_of" in sense or "form-of" in tags
        if not glosses or "no-gloss" in tags or (is_form_of and pos not in CLOSED_CLASS_POS):
            continue
        # Subsenses list the parent gloss first; the last gloss is the specific one.
        compact = {"gloss": glosses[-1], "tags": tags}
        if alt_of := sense.get("alt_of"):
            compact["alt_of"] = alt_of[0].get("word")
        senses.append(compact)
        if len(senses) == MAX_SENSES:
            break
    if not senses:
        return None
    return {"word": entry["word"], "pos": pos, "senses": senses}


@contextmanager
def open_lines(url: str) -> Iterator[Iterator[bytes]]:
    """Stream a JSONL file's lines, decompressing if the server sends gzip."""
    request = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(request) as response:
        gzipped = url.endswith(".gz") or response.headers.get("Content-Encoding") == "gzip"
        if gzipped:
            with gzip.open(response, "rb") as lines:
                yield lines
        else:
            yield response


def filter_spanish(lines: Iterator[bytes], out) -> int:
    kept = 0
    for n, line in enumerate(lines, start=1):
        if n % 1_000_000 == 0:
            print(f"  {n:,} lines scanned, {kept:,} Spanish entries kept", flush=True)
        # Cheap byte check before parsing: only Spanish entries get json-decoded.
        if b'"lang_code": "es"' not in line and b'"lang_code":"es"' not in line:
            continue
        entry = json.loads(line)
        if entry.get("lang_code") != "es":
            continue
        compact = compact_entry(entry)
        if compact:
            out.write(json.dumps(compact, ensure_ascii=False) + "\n")
            kept += 1
    return kept


def fetch_wiktionary(dest: Path) -> None:
    if dest.exists():
        print(f"skip  {dest.name} (exists)")
        return
    partial = dest.with_suffix(dest.suffix + ".part")
    kept = 0
    for url in (KAIKKI_SPANISH_URL, KAIKKI_ALL_URL):
        print(f"fetch {dest.name} from {url}")
        try:
            with open_lines(url) as lines, partial.open("w", encoding="utf-8") as out:
                kept = filter_spanish(lines, out)
            break
        except urllib.error.HTTPError as error:
            if error.code not in (404, 410):
                raise
            print(f"  not available ({error.code}); trying the next source")
    if kept == 0:
        partial.unlink(missing_ok=True)
        sys.exit("No Spanish entries found; the kaikki format may have changed.")
    partial.replace(dest)
    print(f"  done: {kept:,} Spanish entries")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--skip-wiktionary", action="store_true")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        fetch(url, RAW_DIR / name)
    if not args.skip_wiktionary:
        fetch_wiktionary(RAW_DIR / WIKTIONARY_FILE)


if __name__ == "__main__":
    main()
