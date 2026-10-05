"""Multi-word expressions to learn as one item: sin embargo, por favor, echar de menos.

    uv run python -m spanish_tutor.ingest.expressions candidates  # filter + find in Tatoeba
    uv run python -m spanish_tutor.ingest.expressions review      # Claude reviews (Batch API)
    uv run python -m spanish_tutor.ingest.expressions collect     # keep the approved ones

Wiktionary lists about 15,600 multi-word Spanish entries (names and proverbs aside), but
matching them naively is harmful (measured 2026-10-05): the most frequent matches are
function-word pairs (de la, a la, lo que), phrasebook sentences whose words the learner
knows separately (no sé, dónde estás), and wrong senses (la vida is listed only as slang
for prostitution, so every "la vida" would be taught that way). So:

1. Rule filters: drop the phrasebook, article, proverb and name categories; drop senses
   that are literal ("used other than figuratively or idiomatically"), vulgar, derogatory,
   archaic, obsolete, dated, historical or just alternative spellings. Keep entries whose
   lemma sequence occurs in at least MIN_SENTENCES Tatoeba sentences.
2. Review: Claude (Opus 5.5, Batch API, Jason's choice) sees each candidate's remaining
   senses and real Tatoeba sentences, and decides whether it's a fixed expression to learn
   as a unit whose listed meaning is the usual one there, and which sense that is.
3. The kept list (data/processed/expressions.jsonl) drives matching in lexicon.analyze.

Expressions are matched by lemma sequence, so inflection is free ("me di cuenta" matches
darse cuenta); a word inserted inside an expression ("echar mucho de menos") is not.
"""

import argparse
import copy
import json
import sys
import time
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

from spanish_tutor.config import DATA_DIR, MODEL
from spanish_tutor.ingest.download import RAW_DIR, WIKTIONARY_FILE
from spanish_tutor.ingest.tatoeba import ANALYZED_PATH, read_analyzed

PROCESSED = DATA_DIR / "processed"
CANDIDATES_PATH = PROCESSED / "expression_candidates.jsonl"
BATCH_PATH = PROCESSED / "expression_batch.json"
REVIEWS_PATH = PROCESSED / "expression_reviews.jsonl"
EXPRESSIONS_PATH = PROCESSED / "expressions.jsonl"

SKIP_POS = frozenset({"name", "punct", "proverb", "phrase", "article"})
DROP_TAGS = frozenset(
    {"vulgar", "derogatory", "obsolete", "archaic", "dated", "historical", "misspelling", "alt-of"}
)
LITERAL = "Used other than figuratively or idiomatically"
MIN_SENTENCES = 3
EXAMPLES = 4


@dataclass
class Candidate:
    phrase: str
    wiktionary_pos: str
    senses: list[str]  # glosses that survived the rule filters
    lemmas: tuple[str, ...] = ()
    sentences: int = 0
    examples: list[tuple[str, str | None]] = field(default_factory=list)


def wiktionary_entries(path: Path = RAW_DIR / WIKTIONARY_FILE) -> dict[str, Candidate]:
    """Multi-word entries that survive the rule filters, by phrase (first entry wins)."""
    found: dict[str, Candidate] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            phrase = entry["word"].strip()
            if " " not in phrase or entry["pos"] in SKIP_POS:
                continue
            senses = [
                s["gloss"]
                for s in entry["senses"]
                if not s["gloss"].startswith(LITERAL) and not DROP_TAGS & set(s.get("tags", []))
            ]
            if not senses:
                continue
            if phrase in found:
                found[phrase].senses += [s for s in senses if s not in found[phrase].senses]
            else:
                found[phrase] = Candidate(phrase, entry["pos"], senses)
    return found


def sentence_lemmas(tokens, expressions: Mapping[str, tuple[str, ...]] | None = None) -> list[str]:
    """A sentence's lemmas in order; a contraction contributes both (del -> de, el).

    An expression already matched in the corpus (a corpus analyzed with an earlier list)
    contributes its own lemmas, given in `expressions` (phrase -> lemmas), so the
    candidates can be rebuilt after the list exists.
    """
    lemmas: list[str] = []
    for _, analyses in tokens:
        for lemma, pos in analyses:
            if pos == "EXPR" and expressions and lemma in expressions:
                lemmas.extend(expressions[lemma])
            else:
                lemmas.append(lemma)
    return lemmas


def find_in_corpus(
    candidates: list[Candidate],
    sentences,
    expressions: Mapping[str, tuple[str, ...]] | None = None,
) -> None:
    """Count the sentences containing each candidate's lemma sequence; keep a few examples."""
    by_first: dict[str, list[Candidate]] = defaultdict(list)
    for c in candidates:
        by_first[c.lemmas[0]].append(c)
    for sentence in sentences:
        lemmas = sentence_lemmas(sentence.tokens, expressions)
        seen = set()
        for i, lemma in enumerate(lemmas):
            for c in by_first.get(lemma, ()):
                if tuple(lemmas[i : i + len(c.lemmas)]) == c.lemmas and c.phrase not in seen:
                    seen.add(c.phrase)
                    c.sentences += 1
                    readable = 4 <= len(sentence.tokens) <= 14 and sentence.en
                    if readable and len(c.examples) < EXAMPLES:
                        c.examples.append((sentence.es, sentence.en))


def build_candidates() -> list[Candidate]:
    from spanish_tutor.lexicon import analyze, load_corrector

    entries = list(wiktionary_entries().values())
    print(f"{len(entries):,} multi-word entries pass the rule filters")
    phrases = [c.phrase for c in entries]
    # Lemmatize the phrases word by word: with the approved list applied, "sin embargo"
    # would come back as one item.
    corrector = copy.copy(load_corrector())
    corrector.expressions = None
    for c, tokens in zip(entries, analyze(phrases, corrector=corrector), strict=True):
        if all(analyses for _, analyses in tokens):
            c.lemmas = tuple(sentence_lemmas(tokens))
    entries = [c for c in entries if len(c.lemmas) >= 2]
    print(f"{len(entries):,} lemmatize to two or more words; searching Tatoeba ...")
    approved = {e["phrase"]: tuple(e["lemmas"]) for e in load_expressions()}
    find_in_corpus(entries, read_analyzed(ANALYZED_PATH), approved)
    kept = [c for c in entries if c.sentences >= MIN_SENTENCES]
    kept.sort(key=lambda c: (-c.sentences, c.phrase))
    print(f"{len(kept):,} occur in at least {MIN_SENTENCES} sentences")
    return kept


def write_jsonl(path: Path, rows: Iterator[dict] | list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


# --- Review ------------------------------------------------------------------------------

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "keep": {"type": "boolean"},
        "sense": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": ["keep", "sense", "reason"],
    "additionalProperties": False,
}


def review_prompt(c: dict) -> str:
    senses = "\n".join(f"{i}. {gloss}" for i, gloss in enumerate(c["senses"], 1))
    examples = (
        "\n".join(f"- {es}" + (f"  ({en})" if en else "") for es, en in c["examples"])
        or "- (no short translated examples)"
    )
    return (
        "You are checking entries for a Spanish learner's dictionary. The app will treat a "
        "listed phrase as one vocabulary item: wherever its words appear together (in any "
        "inflection), the learner is credited with, or taught, the phrase instead of its "
        "separate words.\n\n"
        f'Phrase: "{c["phrase"]}" (Wiktionary part of speech: {c["wiktionary_pos"]})\n'
        f"Wiktionary senses:\n{senses}\n\n"
        f"Real sentences where these words appear together ({c['sentences']} in the corpus):\n"
        f"{examples}\n\n"
        "Decide:\n"
        "- keep: true only if this is a fixed expression a learner should learn as a unit (its "
        "meaning isn't simply the sum of its words, or it's a set phrase used as one unit, like "
        '"sin embargo", "por favor", "a menudo", "darse cuenta", "fin de semana"), '
        "AND in sentences like these the words together usually carry that meaning. False for "
        'ordinary combinations of words ("de la", "lo que", "no sé", "dónde estás"), '
        "phrases whose words are mostly used literally in such sentences, and senses that are "
        "rare, regional or slang compared with the usual reading.\n"
        "- sense: the number of the listed sense that matches the usual meaning (0 if none).\n"
        "- reason: one short sentence."
    )


def submit_review(candidates: list[dict], model: str = MODEL) -> str:
    import anthropic

    client = anthropic.Anthropic()
    requests = [
        {
            "custom_id": f"expr-{i}",
            "params": {
                "model": model,
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": review_prompt(c)}],
                "output_config": {
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": REVIEW_SCHEMA},
                },
            },
        }
        for i, c in enumerate(candidates)
    ]
    batch = client.messages.batches.create(requests=requests)
    BATCH_PATH.write_text(
        json.dumps({"batch_id": batch.id, "model": model, "count": len(requests)}),
        encoding="utf-8",
    )
    return batch.id


def fetch_reviews(candidates: list[dict], wait: bool = True) -> list[dict] | None:
    """The batch's results joined to the candidates; None if it hasn't finished."""
    import anthropic

    client = anthropic.Anthropic()
    info = json.loads(BATCH_PATH.read_text(encoding="utf-8"))
    while True:
        batch = client.messages.batches.retrieve(info["batch_id"])
        if batch.processing_status == "ended":
            break
        if not wait:
            return None
        print(f"  {batch.request_counts.processing:,} still processing ...", flush=True)
        time.sleep(60)
    reviews, usage = [], {"input": 0, "output": 0}
    for result in client.messages.batches.results(info["batch_id"]):
        c = candidates[int(result.custom_id.split("-", 1)[1])]
        row = {"phrase": c["phrase"], "reviewer": info["model"], "status": result.result.type}
        if result.result.type == "succeeded":
            message = result.result.message
            text = next(b.text for b in message.content if b.type == "text")
            row |= json.loads(text)
            usage["input"] += message.usage.input_tokens
            usage["output"] += message.usage.output_tokens
        reviews.append(row)
    print(f"tokens: {usage['input']:,} in, {usage['output']:,} out")
    return reviews


def collect(candidates: list[dict], reviews: list[dict]) -> list[dict]:
    """The approved expressions, each with the reviewed sense as its definition."""
    by_phrase = {c["phrase"]: c for c in candidates}
    kept = []
    for r in reviews:
        c = by_phrase[r["phrase"]]
        if r.get("keep") and 1 <= r.get("sense", 0) <= len(c["senses"]):
            kept.append(
                {
                    "phrase": c["phrase"],
                    "lemmas": c["lemmas"],
                    "definition_en": c["senses"][r["sense"] - 1],
                    "sentences": c["sentences"],
                }
            )
    kept.sort(key=lambda e: (-e["sentences"], e["phrase"]))
    return kept


def signature(path: Path = EXPRESSIONS_PATH) -> str | None:
    """A short fingerprint of the approved list, recorded with the analyzed corpus."""
    import hashlib

    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def load_expressions(path: Path = EXPRESSIONS_PATH) -> list[dict]:
    """The approved expressions, or [] before they've been built."""
    return read_jsonl(path) if path.exists() else []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("step", choices=["candidates", "review", "collect"])
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.step == "candidates":
        write_jsonl(CANDIDATES_PATH, (asdict(c) for c in build_candidates()))
        print(f"wrote {CANDIDATES_PATH}")
    elif args.step == "review":
        candidates = read_jsonl(CANDIDATES_PATH)
        batch_id = submit_review(candidates)
        print(f"submitted {len(candidates):,} reviews as batch {batch_id}")
    else:
        candidates = read_jsonl(CANDIDATES_PATH)
        reviews = fetch_reviews(candidates)
        write_jsonl(REVIEWS_PATH, reviews)
        kept = collect(candidates, reviews)
        write_jsonl(EXPRESSIONS_PATH, kept)
        print(f"kept {len(kept):,} of {len(reviews):,}; wrote {EXPRESSIONS_PATH}")


if __name__ == "__main__":
    main()
