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

    def parts_of_speech(self, word: str) -> list[str]:
        """Every UPOS the word has its own entry under (no fallbacks)."""
        if not hasattr(self, "_pos_index"):
            self._pos_index: dict[str, list[str]] = defaultdict(list)
            for lemma, upos in self.senses:
                self._pos_index[lemma].append(upos)
        return self._pos_index.get(word, [])

    def form_links(self, path: Path) -> dict[str, list[tuple[str, str]]]:
        """Inflected form -> the dictionary words it's a form of, as (lemma, UPOS).

        Reads the form-of table written by ingest/download.py. Targets that aren't
        themselves dictionary entries are dropped (Wiktionary occasionally links a form
        to an English gloss, e.g. amiga -> "friend").
        """
        links: dict[str, list[tuple[str, str]]] = defaultdict(list)
        with path.open(encoding="utf-8") as f:
            for line in f:
                form, wiktionary_pos, lemma = line.rstrip("\n").split("\t")
                for upos in POS_MAP.get(wiktionary_pos, ()):
                    if (lemma, upos) in self and (lemma, upos) not in links[form]:
                        links[form].append((lemma, upos))
        return dict(links)

    def misspellings(self) -> dict[tuple[str, str], tuple[str, str]]:
        """(misspelling, UPOS) -> the correct word, for entries that are only a misspelling.

        Wiktionary lists common typos as entries ("dia: misspelling of día"), and corpora
        contain them, so without this they become words of their own. An entry qualifies
        only when every sense points to the same correct word and at least one is tagged
        "misspelling" (dia is also an "obsolete spelling" of día). Alternative forms are
        deliberately excluded: "mi: alternative form of mío" and "buen: of bueno" are
        separate words a learner has to learn.
        """
        redirects = {}
        for (word, upos), senses in self.senses.items():
            targets = {s.get("alt_of") for s in senses}
            if len(targets) != 1 or None in targets:
                continue
            if not any("misspelling" in s["tags"] for s in senses):
                continue
            target = targets.pop()
            if target != word and (target, upos) in self:
                redirects[word, upos] = (target, upos)
        return redirects

    def nonstandard_spellings(self) -> set[str]:
        """Spellings that are only misspellings or obsolete spellings of other words.

        "jardin: obsolete spelling of jardín", "dia: misspelling of día". These aren't
        words a learner (or a modern writer) means, so accent restoration may replace
        them. Plain alternative forms (mi of mío, buen of bueno) and archaic ones (sólo)
        are real words and are not included.
        """
        by_word: dict[str, list[dict]] = defaultdict(list)
        for (word, _), senses in self.senses.items():
            by_word[word].extend(senses)
        return {
            word
            for word, senses in by_word.items()
            if all(
                s.get("alt_of") and ("misspelling" in s["tags"] or "obsolete" in s["tags"])
                for s in senses
            )
        }

    def reform_1952_spellings(self) -> dict[str, str]:
        """Spellings the RAE's 1952 reform superseded -> the modern spelling (fué -> fue).

        Texts from before 1952, which is most public-domain books, wrote accents the reform
        dropped: fué, dió, fuí, vió. Wiktionary keeps them as entries ("superseded spelling
        of fue, deprecated in 1952 by the Royal Spanish Academy"), so without this they'd
        be words of their own. The year appears only in the gloss. Later reforms are left
        alone: the 2010 spellings (sólo, guión) are still common in modern text, and the
        learner's word bank uses them.
        """
        by_word: dict[str, list[dict]] = defaultdict(list)
        for (word, _), senses in self.senses.items():
            by_word[word].extend(senses)
        respellings = {}
        for word, senses in by_word.items():
            targets = {s.get("alt_of") for s in senses}
            if (
                len(targets) == 1
                and None not in targets
                and all("deprecated in 1952" in s.get("gloss", "") for s in senses)
            ):
                respellings[word] = targets.pop()
        return respellings

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
