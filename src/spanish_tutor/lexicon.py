"""The single path from Spanish text to (lemma, pos) pairs.

The word bank, the seed pipeline, and (from Phase 3) the content-difficulty index must all
lemmatize text the same way, or "is this word new?" gives wrong answers. Everything that
turns text into vocabulary goes through this module.

Tagging and lemmatization use spaCy's transformer pipeline (MODEL). Its lemmas are
corrected against Wiktionary (LemmaCorrector), so analyzing text needs the downloaded
Wiktionary files: `uv run python -m spanish_tutor.ingest.download`.

Changing MODEL changes every analysis: re-run `ingest.tatoeba` (the cached corpus records
the tagger that made it, and the steps that read it refuse a different one).
"""

import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import spacy
from spacy.language import Language
from spacy.tokens import Token

if TYPE_CHECKING:
    from spanish_tutor.ingest.wiktionary import Wiktionary

# A transformer pipeline (BETO, a Spanish BERT), chosen over es_core_news_md on 2026-10-02.
# On hand-labeled Tatoeba samples of verb forms md had tagged as nouns ("Yo trabajo",
# "No toques", "Cuando compras algo") it found 50 of 51 verbs (md: 0), without tagging
# any of 52 real nouns as verbs. About 10 ms a sentence on CPU; ~6 s to load.
MODEL = "es_dep_news_trf"

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

# Single-word fixes for frequent tagger errors: (form, tagger lemma) -> lemma, keeping the
# tagger's POS. A general rule is always preferred; these are measured exceptions (Jason's
# call). gracias: the transformer files the thanks under gracia ("grace") in 451 of 1,127
# Tatoeba tokens (2026-10-05), and the plural of gracia in that sense is vanishingly rare.
LEMMA_FIXES = {("gracias", "gracia"): "gracias"}

# Closed-class parts of speech: a word's function-word reading wins over a noun sense.
FUNCTION_POS = frozenset(["ADP", "CCONJ", "DET", "PRON", "SCONJ"])

# Where a tagger lemma that is a real word, but not one the form belongs to, is replaced
# by the word the form is listed under (LemmaCorrector._relink).
RELINK_POS = frozenset(["VERB", "ADJ", "DET"])

Analysis = tuple[str, str]  # (lemma, pos)


@cache
def load_nlp() -> Language:
    # The lemmatizer needs the morphologizer's POS and morphology, not the parse. (The
    # transformer model has no NER; "ner" is excluded in case a model with one is used.)
    return spacy.load(MODEL, exclude=["parser", "ner"])


def tagger() -> str:
    """The tagger model and version, as recorded with cached analyses ("es_dep_news_trf 3.8.0")."""
    return f"{MODEL} {spacy.util.get_package_version(MODEL)}"


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

    The opposite case, accents a 1952 spelling reform removed, is fixed the same way:
    `respellings` maps superseded spellings to modern ones (fué -> fue, dió -> dio;
    Wiktionary.reform_1952_spellings), so an old book's "fué" is analyzed exactly like a
    modern "fue" instead of becoming a word of its own (74 times in a 1917 story
    collection, measured 2026-10-05). Known tagger limit, the same for both spellings:
    es_dep_news_trf lemmatizes "fue"/"fueron" as ser even when they mean "went" (all
    7,098 Tatoeba tokens), and "fui"/"fuimos" as ir.
    """

    MIN_COUNT = 20  # SUBTLEX-ESP occurrences, about 0.5 per million words

    def __init__(
        self,
        words: Iterable[str],
        counts: Mapping[str, int],
        min_count: int = MIN_COUNT,
        respellings: Mapping[str, str] | None = None,
    ):
        self.words = set(words)
        self.counts = counts
        self.min_count = min_count
        self.respellings = respellings or {}
        self.by_fold: dict[str, list[str]] = {}
        for word in self.words:
            if word.isalpha():
                self.by_fold.setdefault(word.translate(_FOLD), []).append(word)
        self.restored: Counter[tuple[str, str]] = Counter()  # (as typed, restored), for review

    def restore(self, form: str) -> str | None:
        """The accented word a lowercase `form` stands for, or None.

        Apart from superseded spellings (`respellings`), only forms typed without any marks
        are restored: the restorer adds missing accents, it never changes ones that were
        typed (sudán must not become sudan).
        """
        if (modern := self.respellings.get(form)) is not None:
            return modern
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
    """Repairs tagger lemmas against Wiktionary.

    When the tagger's (lemma, pos) is a dictionary word, it is kept unless the form is
    listed under a different word with that POS ("riego" VERB: regir -> regar; see
    `_relink` for where this applies).

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
        expressions: "ExpressionMatcher | None" = None,
    ):
        self.is_word = is_word
        self.form_links = form_links
        self.prior = prior
        self.parts_of_speech = parts_of_speech  # the POS a word has its own entries under
        # Dictionary entries that are only a misspelling of another word (dia -> día).
        self.misspellings = misspellings or {}
        self.restorer = restorer  # applied to text before tagging, by `analyze`
        self.expressions = expressions  # applied to the analyzed tokens, by `analyze`
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
            return self._relink(form, analysis)
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

    def _relink(self, form: str, analysis: Analysis) -> Analysis:
        """The lemma the dictionary says `form` belongs to, when the tagger picked another word.

        The tagger's lemma can be a real word that the form simply isn't a form of: "riego"
        tagged VERB gets regir, though it is listed only under regar; "verte" gets verter
        instead of ver, and "buena" the separate word buen instead of bueno. If the form is
        listed under other lemmas with the same POS, the most common of those wins (ties
        are reported, not guessed). Measured on Tatoeba (2026-10-02), this applies to:

        - VERB, ADJ and DET. Not PRON: Wiktionary files eso and esto under the old
          accented spellings ése and éste. Not NOUN: its hits were mostly spelling
          variants (zombie, zombi).
        - For VERB, only infinitives count, so a participle stays with its verb (hechos
          is listed under the participle entry "hecho", but its verb is hacer). A pronominal
          entry of the tagger's own verb doesn't count either: quejó stays quejar rather
          than splitting into quejarse.
        """
        lemma, pos = analysis
        if pos not in RELINK_POS or form == lemma:
            return analysis
        linked = [c for c in self.form_links.get(form, []) if c[1] == pos]
        if not linked or analysis in linked:
            return analysis
        if pos == "VERB":
            if (lemma + "se", pos) in linked:
                return analysis
            linked = [c for c in linked if c[0].endswith(("r", "rse"))]
            if not linked:
                return analysis
        if (choice := self._most_common(linked)) is None:
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
def load_expression_matcher() -> "ExpressionMatcher | None":
    """The approved expressions (ingest/expressions.py), or None before they're built."""
    from spanish_tutor.ingest.expressions import load_expressions

    expressions = load_expressions()
    if not expressions:
        return None
    return ExpressionMatcher((e["phrase"], e["lemmas"]) for e in expressions)


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
        restorer=AccentRestorer(
            words | set(form_links), prior, respellings=wiktionary.reform_1952_spellings()
        ),
        expressions=load_expression_matcher(),
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
    lemma = LEMMA_FIXES.get((form, lemma), lemma)
    pos = POS_FOLDS.get(pos, pos)
    # Checked on the form too: the transformer lemmatizes "vos" as "vo" (2026-10-02).
    if pos == "PRON" and (lemma in PERSONAL_PRONOUN_LEMMAS or form in PERSONAL_PRONOUN_FORMS):
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


class ExpressionMatcher:
    """Finds approved multi-word expressions (sin embargo, darse cuenta) in analyzed text.

    Matching is by lemma sequence over consecutive word tokens, so inflection is free
    ("me di cuenta" contains dar + cuenta: darse cuenta) and a contraction counts with
    both its lemmas ("al menos" is a + el + menos). A match must cover whole tokens. The
    longest expression starting at a token wins.

    A matched expression is one vocabulary item: its first token gets the single analysis
    (phrase, "EXPR") and its surface becomes the whole phrase as written; the other
    tokens keep their surface with no analyses, like names. So "sin embargo" credits and
    teaches the expression, never "embargo" (seizure). The approved list comes from
    ingest/expressions.py.
    """

    def __init__(self, expressions: Iterable[tuple[str, Sequence[str]]]):
        self.by_first: dict[str, list[tuple[tuple[str, ...], str]]] = {}
        for phrase, lemmas in expressions:
            key = tuple(lemmas)
            if len(key) >= 2:
                self.by_first.setdefault(key[0], []).append((key, phrase))
        for entries in self.by_first.values():
            entries.sort(key=lambda entry: -len(entry[0]))

    def __len__(self) -> int:
        return sum(len(entries) for entries in self.by_first.values())

    def apply(self, tokens: list[TokenAnalysis]) -> list[TokenAnalysis]:
        lemmas = [[lemma for lemma, _ in analyses] for _, analyses in tokens]
        result = list(tokens)
        i = 0
        while i < len(tokens):
            span = self._match_at(lemmas, i)
            if span is None:
                i += 1
                continue
            end, phrase = span
            written = " ".join(surface for surface, _ in tokens[i:end])
            result[i] = (written, [(phrase, "EXPR")])
            for j in range(i + 1, end):
                result[j] = (tokens[j][0], [])
            i = end
        return result

    def _match_at(self, lemmas: list[list[str]], start: int) -> tuple[int, str] | None:
        if not lemmas[start]:
            return None
        for key, phrase in self.by_first.get(lemmas[start][0], ()):
            position, token = 0, start
            while position < len(key) and token < len(lemmas) and lemmas[token]:
                part = lemmas[token]
                if tuple(part) != key[position : position + len(part)]:
                    break
                position += len(part)
                token += 1
            if position == len(key):
                return token, phrase
        return None


def analyze(
    texts: Iterable[str],
    batch_size: int = 64,  # fastest for the transformer on CPU (measured: 121/s vs 94 at 1000)
    corrector: LemmaCorrector | None | object = DEFAULT,
) -> Iterator[list[TokenAnalysis]]:
    """For each text, yield one (surface form, analyses) entry per word token.

    Punctuation and whitespace are skipped. Other tokens that aren't vocabulary (names,
    digits) are kept with an empty analyses list, so callers can count how often a
    surface form is *not* vocabulary. A contraction yields one token with two analyses.
    Lemmas are corrected against Wiktionary unless `corrector=None` is passed; with a
    corrector, words typed without their accents are restored before tagging, so the
    surface forms are the restored ones ("detras" -> "detrás"), and approved multi-word
    expressions become one item each (ExpressionMatcher).
    """
    if corrector is DEFAULT:
        corrector = load_corrector()
    restorer = getattr(corrector, "restorer", None)
    if restorer is not None:
        texts = (restorer.restore_text(text) for text in texts)
    expressions = getattr(corrector, "expressions", None)
    for doc in load_nlp().pipe(texts, batch_size=batch_size):
        tokens = [
            (normalize_text(token.text), token_analyses(token, corrector))  # type: ignore[arg-type]
            for token in doc
            if not (token.is_punct or token.is_space)
        ]
        yield expressions.apply(tokens) if expressions is not None else tokens


def vocabulary(tokens: list[TokenAnalysis]) -> set[Analysis]:
    """The distinct (lemma, pos) pairs in an analyzed text."""
    return {analysis for _, analyses in tokens for analysis in analyses}
