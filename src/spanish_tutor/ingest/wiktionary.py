"""English Wiktionary definitions for Spanish words, via kaikki.org (CC BY-SA).

Reads the compact file written by ingest/download.py and indexes it by (lemma, UPOS),
the same key the word bank uses.
"""

import json
from collections import defaultdict
from pathlib import Path

# Wiktionary POS name -> UD tags it can correspond to. Names, affixes, symbols etc. are
# not vocabulary and are left out.
POS_MAP: dict[str, tuple[str, ...]] = {
    "noun": ("NOUN",),
    "verb": ("VERB",),
    "adj": ("ADJ",),
    "adv": ("ADV",),
    "pron": ("PRON",),
    "prep": ("ADP",),
    "conj": ("CCONJ", "SCONJ"),
    "det": ("DET",),
    "article": ("DET",),
    "num": ("NUM",),
    "intj": ("INTJ",),
    "particle": ("PART",),
    "phrase": ("EXPR",),
    "proverb": ("EXPR",),
}

# spaCy (trained on AnCora) and Wiktionary classify some function words differently:
# interrogatives like cómo/dónde are PRON to spaCy but adverbs to Wiktionary, and
# mismo/tal are DET vs adjectives. For these classes, the lemma listed under a compatible
# POS counts as a match; the word keeps spaCy's POS, since that's what content indexing
# will produce. Open classes get no fallback: a mismatch there is usually a tagging error.
POS_FALLBACKS: dict[str, tuple[str, ...]] = {
    "PRON": ("ADV", "DET", "ADJ"),
    "DET": ("ADJ", "PRON", "NUM"),
    "NUM": ("ADJ", "DET", "PRON"),
    "ADV": ("INTJ",),
    "INTJ": ("ADV",),
}

# Senses carrying these tags are only used when a word has no other senses.
DISFAVORED_TAGS = frozenset({"obsolete", "archaic", "dated", "rare"})

MAX_PARTS = 3  # "; "-separated pieces in a definition


class Wiktionary:
    def __init__(self, path: Path):
        # (lemma, upos) -> senses, in file order (Wiktionary lists common senses first).
        self.senses: dict[tuple[str, str], list[dict]] = defaultdict(list)
        with path.open(encoding="utf-8") as f:
            for line in f:
                entry = json.loads(line)
                for upos in POS_MAP.get(entry["pos"], ()):
                    self.senses[entry["word"], upos].extend(entry["senses"])

    def lookup(self, lemma: str, pos: str) -> list[dict]:
        """Senses for (lemma, pos), falling back to compatible POS for function words."""
        for candidate in (pos, *POS_FALLBACKS.get(pos, ())):
            if senses := self.senses.get((lemma, candidate)):
                return senses
        return []

    def __contains__(self, key: tuple[str, str]) -> bool:
        return bool(self.lookup(*key))

    def definition(self, lemma: str, pos: str, *, follow_alt: bool = True) -> str | None:
        senses = self.lookup(lemma, pos)
        if not senses:
            return None
        preferred = [s for s in senses if not DISFAVORED_TAGS & set(s["tags"])] or senses
        # "ese: alternative spelling of ése" says little; use the other spelling's senses.
        real = [s for s in preferred if "alt_of" not in s]
        if not real and follow_alt:
            target = preferred[0]["alt_of"]
            if target and (followed := self.definition(target, pos, follow_alt=False)):
                return followed
        parts: list[str] = []
        for sense in real or preferred:
            for part in sense["gloss"].split("; "):
                if part not in parts:
                    parts.append(part)
        return "; ".join(parts[:MAX_PARTS])
