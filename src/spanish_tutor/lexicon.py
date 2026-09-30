"""The single path from Spanish text to (lemma, pos) pairs.

The word bank, the seed pipeline, and (from Phase 3) the content-difficulty index must all
lemmatize text the same way, or "is this word new?" gives wrong answers. Everything that
turns text into vocabulary goes through this module.
"""

import unicodedata
from collections.abc import Iterable, Iterator
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


def normalize(form: str, lemma: str, pos: str) -> list[Analysis]:
    """Map a token's surface form and tagger (lemma, pos) to zero or more vocabulary analyses."""
    lemma = normalize_text(lemma)
    pos = POS_FOLDS.get(pos, pos)
    if pos == "PRON" and lemma in PERSONAL_PRONOUN_LEMMAS:
        lemma = normalize_text(form)
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
    return [(lemma, pos)]


def token_analyses(token: Token) -> list[Analysis]:
    return normalize(token.text, token.lemma_, token.pos_)


TokenAnalysis = tuple[str, list[Analysis]]  # (surface form, analyses)


def analyze(texts: Iterable[str], batch_size: int = 1000) -> Iterator[list[TokenAnalysis]]:
    """For each text, yield one (surface form, analyses) entry per word token.

    Punctuation and whitespace are skipped. Other tokens that aren't vocabulary (names,
    digits) are kept with an empty analyses list, so callers can count how often a
    surface form is *not* vocabulary. A contraction yields one token with two analyses.
    """
    for doc in load_nlp().pipe(texts, batch_size=batch_size):
        yield [
            (normalize_text(token.text), token_analyses(token))
            for token in doc
            if not (token.is_punct or token.is_space)
        ]


def vocabulary(tokens: list[TokenAnalysis]) -> set[Analysis]:
    """The distinct (lemma, pos) pairs in an analyzed text."""
    return {analysis for _, analyses in tokens for analysis in analyses}
