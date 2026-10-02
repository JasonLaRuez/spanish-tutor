"""The single path from Spanish text to (lemma, pos) pairs.

The word bank, the seed pipeline, and (from Phase 3) the content-difficulty index must all
lemmatize text the same way, or "is this word new?" gives wrong answers. Everything that
turns text into vocabulary goes through this module.

spaCy's lemmas are corrected against Wiktionary (LemmaCorrector), so analyzing text needs
the downloaded Wiktionary files: `uv run python -m spanish_tutor.ingest.download`.
"""

import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import spacy
from spacy.language import Language
from spacy.tokens import Token

if TYPE_CHECKING:
    from spanish_tutor.ingest.wiktionary import Wiktionary

MODEL = "es_core_news_md"

# Must match the CHECK constraint on lexemes.pos in sql/schema.sql (minus EXPR, which
# comes from multi-word-expression matching, not single tokens).
VOCAB_POS = frozenset(
    ["ADJ", "ADP", "ADV", "CCONJ", "DET", "INTJ", "NOUN", "NUM", "PART", "PRON", "SCONJ", "VERB"]
)

# spaCy tags copular ser/estar, perfect haber, and existential "hay" as AUX. Folded so
# each of those verbs is one lexeme.
POS_FOLDS = {"AUX": "VERB"}

# Contractions the tokenizer leaves whole: "del" = de + el, "al" = a + el.
CONTRACTIONS = {
    "del": [("de", "ADP"), ("el", "DET")],
    "al": [("a", "ADP"), ("el", "DET")],
}

# spaCy lemmatizes personal pronouns to their person: se/lo/la/le/ella -> "él",
# me/nos/conmigo -> "yo", te/ti/ustedes -> "tú". For a learner these are distinct words
# (knowing "yo" doesn't mean knowing "conmigo"), so personal pronouns keep their own form.
PERSONAL_PRONOUN_LEMMAS = frozenset(
    ["yo", "tú", "vos", "él", "ella", "ello", "nosotros", "nosotras", "vosotros", "vosotras"]
    + ["ellos", "ellas", "usted", "ustedes"]
)

# Forms of the personal pronouns that keep their own form as lemma (see above), plus the
# prepositional forms. Articles (la, lo, los...) are left out: they're ambiguous.
PERSONAL_PRONOUN_FORMS = PERSONAL_PRONOUN_LEMMAS | {"mí", "ti", "conmigo", "contigo", "consigo"}

# Closed-class parts of speech: a word's function-word reading wins over a noun sense.
FUNCTION_POS = frozenset(["ADP", "CCONJ", "DET", "PRON", "SCONJ"])

Analysis = tuple[str, str]  # (lemma, pos)


@cache
def load_nlp() -> Language:
    # The lemmatizer needs the morphologizer's POS and morphology, not the parse or NER.
    return spacy.load(MODEL, exclude=["parser", "ner"])


def normalize_text(text: str) -> str:
    """NFC + lowercase. Accents are kept: si/sí, esta/está, el/él are different words."""
    return unicodedata.normalize("NFC", text).lower().strip()


# Accents, the diaeresis, and the tilde of ñ: what an English keyboard leaves out.
_FOLD = str.maketrans("áéíóúüñ", "aeiouun")


_WORD = re.compile(r"[^\W\d_]+")
# What may precede a sentence's first word: nothing, or the end of a sentence, plus
# opening punctuation and spaces.
_SENTENCE_START = re.compile(r"(?:^|[.!?…:]\s)[\s¿¡\"'«“(\-—]*$")


class AccentRestorer:
    """Puts back the accents a learner (or a corpus author) typed without.

    "detras", "manana" and "comi" aren't Spanish words, but exactly one word or inflected
    form is spelled that way once accents (and ñ) are ignored: detrás, mañana, comí. The
    restorer only touches spellings that aren't words already, so "esta", "ano" and
    "hable" stay as typed: they are real words, and guessing would be wrong as often as
    right. (A spelling that is only a misspelling or obsolete spelling, like "jardin",
    doesn't count as a word.)

    Restoration runs on the text, before tagging, so spaCy sees "detrás" in its sentence
    and tags it correctly. Run on the bare token afterwards, "detras" had already been
    lemmatized as the plural of an invented noun "detra", and a capitalized "Despues"
    tagged as a name and dropped.

    Several candidates are decided by subtitle frequency (niño over the rare ñiño), and a
    tie is never guessed. A restored word must also occur at least `min_count` times in
    the subtitles: measured on Tatoeba (2026-10-02), rarer restorations were mostly junk
    (ana -> aña, canadá -> cañada), while common ones were right (traeme -> tráeme).
    """

    MIN_COUNT = 20  # SUBTLEX-ESP occurrences, about 0.5 per million words

    def __init__(self, words: Iterable[str], counts: Mapping[str, int], min_count: int = MIN_COUNT):
        self.words = set(words)
        self.counts = counts
        self.min_count = min_count
        self.by_fold: dict[str, list[str]] = {}
        for word in self.words:
            if word.isalpha():
                self.by_fold.setdefault(word.translate(_FOLD), []).append(word)
        self.restored: Counter[tuple[str, str]] = Counter()  # (as typed, restored), for review

    def restore(self, form: str) -> str | None:
        """The accented word a lowercase `form` stands for, or None.

        Only forms typed without any marks are restored: the restorer adds missing
        accents, it never changes ones that were typed (sudán must not become sudan).
        """
        if form in self.words or not form.isalpha() or form != form.translate(_FOLD):
            return None
        candidates = sorted(
            self.by_fold.get(form.translate(_FOLD), []), key=lambda w: -self.counts.get(w, 0)
        )
        if not candidates:
            return None
        best = self.counts.get(candidates[0], 0)
        if len(candidates) > 1 and best <= self.counts.get(candidates[1], 0):
            return None  # a tie: don't guess
        return candidates[0] if best >= self.min_count else None

    def restore_text(self, text: str) -> str:
        """`text` with each accentless word restored, keeping its capitalization.

        A capitalized word is restored only at the start of a sentence ("Despues, ...").
        Elsewhere a capital marks a name: "País de Gales" must not become "galés".
        """

        def replace(match: re.Match[str]) -> str:
            typed = match.group()
            if typed[0].isupper() and not _SENTENCE_START.search(text[: match.start()]):
                return typed
            if (word := self.restore(normalize_text(typed))) is None:
                return typed
            self.restored[typed.lower(), word] += 1
            if typed.isupper() and len(typed) > 1:
                return word.upper()
            return word[0].upper() + word[1:] if typed[0].isupper() else word

        return _WORD.sub(replace, unicodedata.normalize("NFC", text))


class LemmaCorrector:
    """Repairs tagger lemmas that aren't dictionary words, using Wiktionary.

    spaCy sometimes leaves an inflected form as the lemma ("crees", "dólares") or invents
    one ("debería" -> "deberiar"). When the tagger's (lemma, pos) isn't a dictionary word:

    1. If the surface form itself is a dictionary word with that POS, use it.
    2. If the form (or the tagger's lemma) is a listed form of dictionary words with that
       POS, use one: "has" -> haber. Several are resolved by `prior` (how common each
       lemma's citation form is), e.g. "crees" -> creer rather than crear.
    3. If the surface form is a dictionary word under exactly one other POS, the tag was
       wrong: "en serio" tagged NOUN -> serio ADJ (not the verb seriar it's a form of).
    4. Otherwise a listed form of dictionary words with another POS, resolved as in 2.
    5. Anything still unresolved is left as is; downstream Wiktionary checks reject it.
       Ties are never guessed.

    Words typed without their accents are fixed earlier, in the text itself, by the
    corrector's `restorer` (see AccentRestorer and `analyze`).

    Corrections and unresolved cases are counted for review.
    """

    def __init__(
        self,
        is_word: Callable[[Analysis], bool],
        form_links: Mapping[str, list[Analysis]],
        prior: Mapping[str, int],
        parts_of_speech: Callable[[str], list[str]] = lambda word: [],
        misspellings: Mapping[Analysis, Analysis] | None = None,
        restorer: AccentRestorer | None = None,
    ):
        self.is_word = is_word
        self.form_links = form_links
        self.prior = prior
        self.parts_of_speech = parts_of_speech  # the POS a word has its own entries under
        # Dictionary entries that are only a misspelling of another word (dia -> día).
        self.misspellings = misspellings or {}
        self.restorer = restorer  # applied to text before tagging, by `analyze`
        self.corrected: Counter[tuple[str, Analysis, Analysis]] = Counter()
        self.unresolved: Counter[tuple[str, Analysis]] = Counter()

    def correct(self, form: str, analysis: Analysis) -> Analysis:
        result = self._correct(form, analysis)
        if (respelled := self._respell(result)) != result:
            return self._record(form, analysis, respelled)
        return result

    def _respell(self, analysis: Analysis) -> Analysis:
        """The word a misspelling-only dictionary entry stands for; anything else unchanged.

        Wiktionary lists common typos as entries (dia, rio, tambien, aser), so they pass
        the dictionary check and would become words of their own. The correct spelling may
        itself be an inflected form (habia -> había -> haber), so it is corrected in turn,
        without adding that internal step to the corrections report.
        """
        if (target := self.misspellings.get(analysis)) is None:
            return analysis
        corrected, unresolved = self.corrected.copy(), self.unresolved.copy()
        fixed = self._correct(target[0], target)
        self.corrected, self.unresolved = corrected, unresolved
        return self.misspellings.get(fixed, fixed)

    def _correct(self, form: str, analysis: Analysis) -> Analysis:
        lemma, pos = analysis
        if self.is_word(analysis):
            return analysis
        if self.is_word((form, pos)):
            return self._record(form, analysis, (form, pos))
        candidates = list(
            dict.fromkeys(self.form_links.get(form, []) + self.form_links.get(lemma, []))
        )
        same_pos = [c for c in candidates if c[1] == pos]
        if same_pos:
            choice = self._most_common(same_pos)
        else:
            other_pos = [p for p in self.parts_of_speech(form) if p != pos]
            if len(other_pos) == 1:
                return self._record(form, analysis, (form, other_pos[0]))
            choice = self._most_common(candidates)
        if choice is None:
            self.unresolved[form, analysis] += 1
            return analysis
        return self._record(form, analysis, choice)

    def retag_proper_noun(self, form: str) -> Analysis | None:
        """The dictionary reading of a lowercase word the tagger called a proper noun.

        spaCy tags some common words PROPN even lowercase mid-sentence ("Me gusta la
        nube", "el perro"), and PROPN is not vocabulary, so they'd silently vanish
        (measured 2026-10-01: 8.5k Tatoeba tokens, 1,652 distinct words). Names are
        capitalized, so a lowercase PROPN that the dictionary knows is a tagging error:

        1. A personal pronoun form is a pronoun (contigo, which Wiktionary files as an
           adverb, and which normal tagging makes PRON).
        2. If the headword has a function-word POS, only those count: the noun senses
           of mi, yo, a are names of musical notes and letters, not what a sentence means.
        3. The form is a headword under one (remaining) POS: use it (madre, llover, tu).
        4. A headword under several POS, NOUN among them: NOUN, since the tagger saw it
           in a noun slot (perro, rosa).
        5. Not a headword: an inflected form, resolved as in `correct` (quieres -> querer).
        Anything else (several non-noun POS, tied lemmas) is reported, not guessed; words
        the dictionary doesn't know at all (foreign words, typos) return None silently.
        """
        before = (form, "PROPN")
        if form in PERSONAL_PRONOUN_FORMS:
            return self._record(form, before, (form, "PRON"))
        own = [pos for pos in self.parts_of_speech(form) if pos in VOCAB_POS]
        own = [pos for pos in own if pos in FUNCTION_POS] or own
        links = [c for c in self.form_links.get(form, []) if c[1] in VOCAB_POS]
        if len(own) == 1:
            choice: Analysis | None = (form, own[0])
        elif "NOUN" in own:
            choice = (form, "NOUN")
        elif own:
            choice = None
        elif links:
            choice = self._most_common(links)
        else:
            return None
        if choice is None:
            self.unresolved[form, before] += 1
            return None
        return self._record(form, before, self._respell(choice))

    def _most_common(self, candidates: list[Analysis]) -> Analysis | None:
        if len(candidates) == 1:
            return candidates[0]
        ranked = sorted(candidates, key=lambda c: self.prior.get(c[0], 0), reverse=True)
        if len(ranked) > 1 and self.prior.get(ranked[0][0], 0) > self.prior.get(ranked[1][0], 0):
            return ranked[0]
        return None  # no candidates, or a tie: don't guess

    def _record(self, form: str, before: Analysis, after: Analysis) -> Analysis:
        self.corrected[form, before, after] += 1
        return after


def _require(*paths: Path) -> None:
    if missing := [p.name for p in paths if not p.exists()]:
        raise FileNotFoundError(
            f"Missing {', '.join(missing)}. Run `uv run python -m spanish_tutor.ingest.download`."
        )


@cache
def load_wiktionary() -> "Wiktionary":
    """The downloaded Wiktionary, loaded once and shared (the corrector, new lexemes)."""
    from spanish_tutor.ingest.download import RAW_DIR, WIKTIONARY_FILE
    from spanish_tutor.ingest.wiktionary import Wiktionary

    _require(RAW_DIR / WIKTIONARY_FILE)
    return Wiktionary(RAW_DIR / WIKTIONARY_FILE)


@cache
def load_corrector() -> LemmaCorrector:
    """The corrector built from the downloaded Wiktionary and SUBTLEX-ESP files."""
    from spanish_tutor.ingest import subtlex
    from spanish_tutor.ingest.download import RAW_DIR, WIKTIONARY_FORMS_FILE

    forms, counts = RAW_DIR / WIKTIONARY_FORMS_FILE, RAW_DIR / "SUBTLEX-ESP.xlsx"
    _require(forms, counts)
    wiktionary = load_wiktionary()
    form_links = wiktionary.form_links(forms)
    misspellings = wiktionary.misspellings()
    prior = subtlex.load_counts(counts)
    # Every spelling that is a real word: headwords and inflected forms, except spellings
    # that are only misspellings or obsolete spellings (dia, jardin), which get restored.
    words = {lemma for lemma, _ in wiktionary.senses} - wiktionary.nonstandard_spellings()
    return LemmaCorrector(
        is_word=lambda analysis: analysis in wiktionary,
        form_links=form_links,
        prior=prior,
        parts_of_speech=wiktionary.parts_of_speech,
        misspellings=misspellings,
        restorer=AccentRestorer(words | set(form_links), prior),
    )


def normalize(
    form: str, lemma: str, pos: str, corrector: LemmaCorrector | None = None
) -> list[Analysis]:
    """Map a token's surface form and tagger (lemma, pos) to zero or more vocabulary analyses.

    `form` is the token as written: its capitalization tells a name ("Juan") from a
    common word mis-tagged as one ("perro"), which the corrector re-tags.
    """
    written_lowercase = form[:1].islower()
    form = normalize_text(form)
    if pos == "PROPN":
        if written_lowercase and corrector is not None:
            return [found] if (found := corrector.retag_proper_noun(form)) else []
        return []
    lemma = normalize_text(lemma)
    pos = POS_FOLDS.get(pos, pos)
    if pos == "PRON" and lemma in PERSONAL_PRONOUN_LEMMAS:
        lemma = form
    # Expanded whatever the tag: the tagger sometimes calls "del" a DET.
    if lemma in CONTRACTIONS:
        return list(CONTRACTIONS[lemma])
    # Clitic verbs can get a space-joined lemma ("dar yo él"); the verb is the first part.
    # Its pronouns are separate words the learner may or may not know; they are not
    # counted here because the tagger's pronoun lemmas for clitics are unreliable.
    if pos == "VERB" and " " in lemma:
        lemma = lemma.split(" ", 1)[0]
    if pos not in VOCAB_POS or not lemma or not any(ch.isalpha() for ch in lemma):
        return []
    if corrector is not None:
        return [corrector.correct(form, (lemma, pos))]
    return [(lemma, pos)]


# Sentinel: "use the standard corrector". Pass None explicitly for raw spaCy behavior.
DEFAULT = object()


def token_analyses(token: Token, corrector: LemmaCorrector | None = None) -> list[Analysis]:
    return normalize(token.text, token.lemma_, token.pos_, corrector)


TokenAnalysis = tuple[str, list[Analysis]]  # (surface form, analyses)


def analyze(
    texts: Iterable[str],
    batch_size: int = 1000,
    corrector: LemmaCorrector | None | object = DEFAULT,
) -> Iterator[list[TokenAnalysis]]:
    """For each text, yield one (surface form, analyses) entry per word token.

    Punctuation and whitespace are skipped. Other tokens that aren't vocabulary (names,
    digits) are kept with an empty analyses list, so callers can count how often a
    surface form is *not* vocabulary. A contraction yields one token with two analyses.
    Lemmas are corrected against Wiktionary unless `corrector=None` is passed; with a
    corrector, words typed without their accents are restored before tagging, so the
    surface forms are the restored ones ("detras" -> "detrás").
    """
    if corrector is DEFAULT:
        corrector = load_corrector()
    restorer = getattr(corrector, "restorer", None)
    if restorer is not None:
        texts = (restorer.restore_text(text) for text in texts)
    for doc in load_nlp().pipe(texts, batch_size=batch_size):
        yield [
            (normalize_text(token.text), token_analyses(token, corrector))  # type: ignore[arg-type]
            for token in doc
            if not (token.is_punct or token.is_space)
        ]


def vocabulary(tokens: list[TokenAnalysis]) -> set[Analysis]:
    """The distinct (lemma, pos) pairs in an analyzed text."""
    return {analysis for _, analyses in tokens for analysis in analyses}
