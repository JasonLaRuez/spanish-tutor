"""Word forms nobody knows, resolved by a bilingual model (roadmap Phase 3, Jason's choice).

A song or book uses words the general lexicon lacks, and Wiktionary doesn't know some of
them either: old spellings in public-domain books (fué, dió, á), elisions in lyrics (pa',
na'), slang and regionalisms, and things that aren't Spanish at all (English lines,
"la la la"). Claude sees each such form with the sentence it appears in and the tagger's
guess, and gives one of three verdicts:

- variant:     another spelling of a word that exists, which is then counted as that word
               (fué -> ser), so known words aren't counted as new;
- word:        a real word missing from the dictionaries, added to `lexemes` with Claude's
               definition, labeled model-written; its example is the real sentence where it
               was met, with Claude's translation;
- not_spanish: excluded from the vocabulary, and counted on the item as unresolved.

The model only proposes: `apply` checks every claim against the database before writing,
so a wrong answer can cost a missed word, never a corrupt lexicon. Every decision is
stored in word_resolutions, which is also the cache: each form is sent once, ever.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from spanish_tutor.lexicon import POS_FOLDS, VOCAB_POS, normalize_text
from spanish_tutor.words import LexiconIndex

POS_TAGS = sorted(VOCAB_POS | {"EXPR"})
MAX_FORMS_PER_CALL = 60  # keeps each structured answer well inside max_tokens


@dataclass(frozen=True)
class Pending:
    """A form no dictionary knows, with what the resolver needs to judge it."""

    form: str  # as written, normalized
    lemma: str  # the tagger's guess
    pos: str
    context: str  # the first sentence it appears in
    occurrences: int

    @property
    def key(self) -> tuple[str, str, str]:
        return self.form, self.lemma, self.pos


class WordResolution(BaseModel):
    """Claude's verdict on one form."""

    id: int = Field(description="The number of the form, as listed.")
    verdict: Literal["word", "variant", "not_spanish"]
    # Required but nullable, like TutorReply.note_en: the schema shape the API accepts.
    lemma: str | None = Field(
        description="The dictionary form of the Spanish word this is (for a variant, the "
        "existing word's dictionary form). Null if not Spanish."
    )
    pos: str | None = Field(description=f"One of: {', '.join(POS_TAGS)}. Null if not Spanish.")
    definition_en: str | None = Field(
        description="Only for verdict 'word': a short English definition, dictionary style. "
        "Null otherwise."
    )
    example_en: str | None = Field(
        description="Only for verdict 'word': a natural English translation of the sentence "
        "the form appears in. Null otherwise."
    )
    reason: str = Field(description="One short sentence in English: why this verdict.")


class Resolutions(BaseModel):
    resolutions: list[WordResolution]


def resolution_prompt(kind: str, pending: Sequence[Pending]) -> str:
    listing = "\n".join(
        f'{i}. "{p.form}" (tagger guessed {p.lemma}|{p.pos}) in: «{p.context}»'
        for i, p in enumerate(pending, 1)
    )
    return (
        f"These words appear in a {kind} a Spanish learner is about to read or hear. A "
        "Spanish dictionary doesn't list them as written. For each, decide what it is:\n"
        "- variant: another spelling of a Spanish word that exists: an old or "
        "pre-reform spelling (fué → ser, á → a), an elision (pa' → para), a regional or "
        "informal spelling, or a typo. Give that word's dictionary form and part of "
        "speech. Prefer this whenever it applies.\n"
        "- word: a real Spanish word (slang, regional, technical, a rare inflection's "
        "lemma) that dictionaries miss. Give its dictionary form, its part of speech, a "
        "short English definition for this sense, and a translation of the sentence.\n"
        "- not_spanish: English or another language, a sound or vocalization "
        "(la la la, oh), a name, or not a word.\n"
        f"Parts of speech: {', '.join(POS_TAGS)} (EXPR is a multi-word expression). Answer "
        "every number exactly once.\n\n" + listing
    )


@dataclass(frozen=True)
class Asked:
    """What a resolver returns: the verdicts, and the request's cost."""

    resolutions: list[WordResolution]
    input_tokens: int = 0
    output_tokens: int = 0


Resolver = Callable[[str, Sequence[Pending]], Asked]


class ClaudeResolver:
    """The real resolver: Claude Opus 5.5 with structured output, MAX_FORMS_PER_CALL
    forms per request (an item's forms usually fit in one)."""

    def __init__(self, model: str | None = None, max_tokens: int = 16000):
        from spanish_tutor.config import MODEL
        from spanish_tutor.conversation import ClaudeGenerator

        self.model = model or MODEL
        self.generator = ClaudeGenerator(self.model, effort="low", max_tokens=max_tokens)

    @property
    def reviewer(self) -> str:
        return f"model:{self.model}"

    def __call__(self, kind: str, pending: Sequence[Pending]) -> Asked:
        resolutions, tokens_in, tokens_out = [], 0, 0
        for start in range(0, len(pending), MAX_FORMS_PER_CALL):
            chunk = pending[start : start + MAX_FORMS_PER_CALL]
            answer = self.generator.ask(Resolutions, resolution_prompt(kind, chunk))
            # Numbers are per request; shift them to positions in the whole list.
            for r in answer.reply.resolutions:
                resolutions.append(r.model_copy(update={"id": r.id + start}))
            tokens_in += answer.input_tokens + answer.cache_read_tokens
            tokens_out += answer.output_tokens
        return Asked(resolutions, tokens_in, tokens_out)


@dataclass(frozen=True)
class Outcome:
    """What happened to one pending form, for the review CSV."""

    pending: Pending
    verdict: str  # word | variant | not_spanish | rejected | unasked
    lexeme_id: int | None
    lemma: str | None
    pos: str | None
    reason: str


def apply(
    conn,
    index: LexiconIndex,
    content_id: int,
    pending: Sequence[Pending],
    resolutions: Sequence[WordResolution],
    reviewer: str,
) -> list[Outcome]:
    """Check each verdict and record it; one Outcome per pending form, in order.

    Checks: answers to numbers that weren't asked (or asked twice) are ignored; a part of
    speech outside the schema's list, a variant of a word that exists nowhere, or a new word
    without a definition is rejected, and the form stays unresolved (not stored, so a later
    run asks again); a "new" word that already exists is recorded as a variant of it.
    Writes in the caller's transaction.
    """
    answers: dict[int, WordResolution] = {}
    for r in resolutions:
        answers.setdefault(r.id, r)
    outcomes = []
    for number, p in enumerate(pending, 1):
        r = answers.get(number)
        if r is None:
            outcomes.append(Outcome(p, "unasked", None, None, None, "no answer"))
            continue
        outcomes.append(_apply_one(conn, index, content_id, p, r, reviewer))
    return outcomes


def _apply_one(conn, index, content_id, p: Pending, r: WordResolution, reviewer) -> Outcome:
    def record(verdict: str, lexeme_id: int | None, reason: str) -> None:
        conn.execute(
            """
            INSERT INTO word_resolutions
                (form, tagged_lemma, tagged_pos, verdict, lexeme_id, reason, reviewer, content_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*p.key, verdict, lexeme_id, reason, reviewer, content_id),
        )

    if r.verdict == "not_spanish":
        record("not_spanish", None, r.reason)
        return Outcome(p, "not_spanish", None, None, None, r.reason)

    lemma = normalize_text(r.lemma or "")
    # Folded like the tagger's tags (AUX -> VERB): the model answers "habíase" with haber|AUX.
    pos = POS_FOLDS.get(r.pos or "", r.pos)
    if not lemma or pos not in POS_TAGS:
        return Outcome(p, "rejected", None, lemma, r.pos, f"invalid lemma or POS: {r.reason}")
    r = r.model_copy(update={"lemma": lemma, "pos": pos})
    analysis = (lemma, pos)
    existing = index.find(analysis) or (
        index.resolve(analysis) if index.in_dictionary(analysis) else None
    )
    if existing is not None:
        reason = r.reason if r.verdict == "variant" else f"already a word: {r.reason}"
        record("variant", existing.lexeme_id, reason)
        return Outcome(p, "variant", existing.lexeme_id, lemma, r.pos, reason)
    if r.verdict == "variant":
        return Outcome(p, "rejected", None, lemma, r.pos, f"a variant of no known word: {r.reason}")
    if not (r.definition_en or "").strip():
        return Outcome(p, "rejected", None, lemma, r.pos, f"no definition: {r.reason}")

    lexeme_id = conn.execute(
        """
        INSERT INTO lexemes (lemma, pos, definition_en, definition_source, example_es,
                             example_en, example_source, example_en_source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lemma,
            r.pos,
            r.definition_en.strip(),
            reviewer,
            p.context,
            (r.example_en or "").strip() or None,
            f"content:{content_id}",
            reviewer if (r.example_en or "").strip() else None,
        ),
    ).lastrowid
    index.add(lexeme_id, lemma, r.pos)
    record("word", lexeme_id, r.reason)
    return Outcome(p, "word", lexeme_id, lemma, r.pos, r.reason)
