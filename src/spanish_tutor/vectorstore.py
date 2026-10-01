"""Example-sentence vector store (Chroma) with vocabulary-constrained search.

    uv run python -m spanish_tutor.vectorstore "query" [--max-unknown N] [-k K]
        Search against the learner's real word bank and print what comes back.

Each Tatoeba sentence is stored with its (lemma, pos) set, computed by the shared
lexicon.py, so search can keep only sentences the learner can read (max_unknown=0) or
that stretch them by exactly one word (max_unknown=1, Krashen's i+1). The filter runs in
Python after the similarity search for now; Phase 3's SQL difficulty index is its
long-term home.
"""

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import chromadb
from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings

from spanish_tutor.config import CHROMA_DIR
from spanish_tutor.embeddings import MODEL_ID, MODEL_REVISION, load_embeddings
from spanish_tutor.lexicon import Analysis

COLLECTION = "tatoeba"


class EmbeddingModelMismatch(RuntimeError):
    """The collection was built with a different embedding model than the one in use."""


def encode_vocab(vocab: Iterable[Analysis]) -> str:
    """Chroma metadata values must be scalars, so a vocabulary set is stored as a string:
    "casa|NOUN;sin embargo|EXPR". Lemmas can contain spaces (multi-word expressions),
    so items are separated by ';'."""
    return ";".join(sorted(f"{lemma}|{pos}" for lemma, pos in vocab))


def decode_vocab(encoded: str) -> set[Analysis]:
    return {tuple(item.split("|", 1)) for item in encoded.split(";") if item}  # type: ignore[misc]


def open_store(
    persist_directory: Path = CHROMA_DIR,
    embeddings: Embeddings | None = None,
    model_id: str = MODEL_ID,
    model_revision: str = MODEL_REVISION,
) -> Chroma:
    """Open (or create) the sentence collection, refusing one built with another model.

    Vectors from different models live in different spaces; mixing them, or querying
    one model's vectors with another's, returns plausible-looking garbage.
    """
    client = chromadb.PersistentClient(path=str(persist_directory))
    expected = {"embedding_model": model_id, "embedding_revision": model_revision}
    existing = {c.name for c in client.list_collections()}
    if COLLECTION in existing:
        metadata = client.get_collection(COLLECTION).metadata or {}
        found = {key: metadata.get(key) for key in expected}
        if found != expected:
            raise EmbeddingModelMismatch(
                f"Collection {COLLECTION!r} was built with {found}, not {expected}. "
                f"Delete {persist_directory} and re-index to switch models."
            )
    return Chroma(
        collection_name=COLLECTION,
        embedding_function=embeddings or load_embeddings(),
        client=client,
        collection_metadata=expected,
        collection_configuration={"hnsw": {"space": "cosine"}},
    )


@dataclass(frozen=True)
class SentenceHit:
    sentence_id: int
    es: str
    en: str
    author: str | None
    similarity: float
    unknown: frozenset[Analysis]  # words outside the learner's vocabulary


def search_sentences(
    store: Chroma,
    query: str,
    known: set[Analysis],
    *,
    k: int = 5,
    max_unknown: int = 0,
    fetch_k: int = 50,
) -> list[SentenceHit]:
    """The k most similar sentences with at most `max_unknown` words the learner lacks.

    Over-fetches `fetch_k` by similarity, then filters by vocabulary, keeping similarity
    order. May return fewer than k when little nearby text is comprehensible; callers
    can then relax max_unknown or broaden the query.
    """
    hits = []
    for doc, distance in store.similarity_search_with_score(query, k=fetch_k):
        similarity = 1.0 - distance  # cosine distance -> cosine similarity, in [-1, 1]
        unknown = decode_vocab(doc.metadata["vocab"]) - known
        if len(unknown) > max_unknown:
            continue
        hits.append(
            SentenceHit(
                sentence_id=doc.metadata["sentence_id"],
                es=doc.page_content,
                en=doc.metadata["en"],
                author=doc.metadata.get("author"),
                similarity=similarity,
                unknown=frozenset(unknown),
            )
        )
        if len(hits) == k:
            break
    return hits


def main() -> None:
    from spanish_tutor import db

    parser = argparse.ArgumentParser(description="Search example sentences.")
    parser.add_argument("query")
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--max-unknown", type=int, default=0)
    args = parser.parse_args()

    conn = db.connect()
    try:
        known = db.known_vocabulary(conn)
    finally:
        conn.close()
    store = open_store()
    hits = search_sentences(store, args.query, known, k=args.k, max_unknown=args.max_unknown)
    if not hits:
        print("No comprehensible matches; try --max-unknown 1.")
    for hit in hits:
        new = ", ".join(f"{lemma} ({pos})" for lemma, pos in sorted(hit.unknown))
        print(f"{hit.similarity:.3f}  {hit.es}")
        print(f"       {hit.en}" + (f"\n       new: {new}" if new else ""))


if __name__ == "__main__":
    main()
