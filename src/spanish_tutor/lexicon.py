"""The single path from Spanish text to (lemma, pos) pairs.

The word bank, the seed pipeline, and (from Phase 3) the content-difficulty index must all
lemmatize text the same way, or "is this word new?" gives wrong answers. Everything that
turns text into vocabulary goes through this module.

spaCy's lemmas are corrected against Wiktionary (LemmaCorrector), so analyzing text needs
the downloaded Wiktionary files: `uv run python -m spanish_tutor.ingest.download`.
"""

import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from functools import cache

import spacy
from spacy.language import Language
from spacy.tokens import Token

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

Analysis = tuple[str, str]  # (lemma, pos)


@cache
def load_nlp() -> Language:
    # The lemmatizer needs the morphologizer's POS and morphology, not the parse or NER.
    return spacy.load(MODEL, exclude=["parser", "ner"])


def normalize_text(text: str) -> str:
    """NFC + lowercase. Accents are kept: si/sí, esta/está, el/él are different words."""
    return unicodedata.normalize("NFC", text).lower().strip()


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

    Corrections and unresolved cases are counted for review.
    """

    def __init__(
        self,
        is_word: Callable[[Analysis], bool],
        form_links: Mapping[str, list[Analysis]],
        prior: Mapping[str, int],
        parts_of_speech: Callable[[str], list[str]] = lambda word: [],
    ):
        self.is_word = is_word
        self.form_links = form_links
        self.prior = prior
        self.parts_of_speech = parts_of_speech  # the POS a word has its own entries under
        self.corrected: Counter[tuple[str, Analysis, Analysis]] = Counter()
        self.unresolved: Counter[tuple[str, Analysis]] = Counter()

    def correct(self, form: str, analysis: Analysis) -> Analysis:
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


@cache
def load_corrector() -> LemmaCorrector:
    """The corrector built from the downloaded Wiktionary and SUBTLEX-ESP files."""
    from spanish_tutor.ingest import subtlex
    from spanish_tutor.ingest.download import RAW_DIR, WIKTIONARY_FILE, WIKTIONARY_FORMS_FILE
    from spanish_tutor.ingest.wiktionary import Wiktionary

    paths = [
        RAW_DIR / WIKTIONARY_FILE,
        RAW_DIR / WIKTIONARY_FORMS_FILE,
        RAW_DIR / "SUBTLEX-ESP.xlsx",
    ]
    if missing := [p.name for p in paths if not p.exists()]:
        raise FileNotFoundError(
            f"Missing {', '.join(missing)}. Run `uv run python -m spanish_tutor.ingest.download`."
        )
    wiktionary = Wiktionary(paths[0])
    return LemmaCorrector(
        is_word=lambda analysis: analysis in wiktionary,
        form_links=wiktionary.form_links(paths[1]),
        prior=subtlex.load_counts(paths[2]),
        parts_of_speech=wiktionary.parts_of_speech,
    )


def normalize(
    form: str, lemma: str, pos: str, corrector: LemmaCorrector | None = None
) -> list[Analysis]:
    """Map a token's surface form and tagger (lemma, pos) to zero or more vocabulary analyses."""
    form = normalize_text(form)
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
    Lemmas are corrected against Wiktionary unless `corrector=None` is passed.
    """
    if corrector is DEFAULT:
        corrector = load_corrector()
    for doc in load_nlp().pipe(texts, batch_size=batch_size):
        yield [
            (normalize_text(token.text), token_analyses(token, corrector))  # type: ignore[arg-type]
            for token in doc
            if not (token.is_punct or token.is_space)
        ]


def vocabulary(tokens: list[TokenAnalysis]) -> set[Analysis]:
    """The distinct (lemma, pos) pairs in an analyzed text."""
    return {analysis for _, analyses in tokens for analysis in analyses}
