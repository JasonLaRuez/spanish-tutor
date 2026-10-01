"""Embed translated Tatoeba sentences into the Chroma store.

    uv run python -m spanish_tutor.ingest.index_tatoeba [--limit N]

Reads the lemmatized cache written by ingest/tatoeba.py, so each stored sentence
carries the same (lemma, pos) vocabulary the word bank uses. Ids are deterministic
("tatoeba:<id>") and already-stored ids are skipped, so an interrupted run resumes
where it stopped and a re-run adds nothing. The full ~261k sentences take ~35 min on CPU.
"""

import argparse
import itertools
import sys
import time
from collections.abc import Callable, Iterable

from langchain_chroma import Chroma

from spanish_tutor.ingest.tatoeba import ANALYZED_PATH, AnalyzedSentence, read_analyzed
from spanish_tutor.lexicon import vocabulary
from spanish_tutor.vectorstore import encode_vocab, open_store

BATCH_SIZE = 512


def document_id(sentence: AnalyzedSentence) -> str:
    return f"tatoeba:{sentence.id}"


def metadata(sentence: AnalyzedSentence) -> dict:
    meta = {
        "source": "tatoeba",
        "sentence_id": sentence.id,
        "en": sentence.en,
        "n_tokens": len(sentence.tokens),
        "vocab": encode_vocab(vocabulary(sentence.tokens)),
    }
    if sentence.author:  # Chroma metadata can't hold None
        meta["author"] = sentence.author
    return meta


def index_sentences(
    store: Chroma,
    sentences: Iterable[AnalyzedSentence],
    *,
    batch_size: int = BATCH_SIZE,
    total: int | None = None,
    report: Callable[[str], None] = print,
) -> tuple[int, int]:
    """Add translated sentences not already stored. Returns (added, skipped)."""
    added = skipped = 0
    started = time.perf_counter()
    translated = (s for s in sentences if s.en)
    for batch in itertools.batched(translated, batch_size):
        ids = [document_id(s) for s in batch]
        stored = set(store.get(ids=ids, include=[])["ids"])
        new = [s for s in batch if document_id(s) not in stored]
        skipped += len(batch) - len(new)
        if new:
            store.add_texts(
                texts=[s.es for s in new],
                metadatas=[metadata(s) for s in new],
                ids=[document_id(s) for s in new],
            )
            added += len(new)
        done = added + skipped
        rate = added / (time.perf_counter() - started) if added else 0
        eta = f", ~{(total - done) / rate / 60:.0f} min left" if total and rate else ""
        report(f"  {done:,}{f' / {total:,}' if total else ''} ({added:,} added{eta})")
    return added, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="Embed Tatoeba sentences into Chroma.")
    parser.add_argument("--limit", type=int, help="only the first N translated sentences")
    args = parser.parse_args()

    if not ANALYZED_PATH.exists():
        sys.exit(
            "Missing the lemmatized corpus. Run `uv run python -m spanish_tutor.ingest.tatoeba`."
        )
    total = sum(1 for s in read_analyzed(ANALYZED_PATH) if s.en)
    if args.limit:
        total = min(total, args.limit)
    sentences = itertools.islice((s for s in read_analyzed(ANALYZED_PATH) if s.en), total)

    print("loading embedding model ...")
    store = open_store()
    print(f"indexing {total:,} translated sentences")
    added, skipped = index_sentences(store, sentences, total=total)
    print(f"done: {added:,} added, {skipped:,} already stored")


if __name__ == "__main__":
    main()
