"""Resolving forms nobody knows (resolve.py): every model claim is checked before writing.

The model is scripted: no API calls. Uses the content fakes (fakes.py): a fake analyzer and lexicon.
"""

import pytest
from fakes import add_content_lexicon, story, vocab
from fakes import index_content as index

from spanish_tutor.resolve import (
    MAX_FORMS_PER_CALL,
    Asked,
    ClaudeResolver,
    Pending,
    Resolutions,
    WordResolution,
    resolution_prompt,
)


@pytest.fixture
def lexicon(conn):
    return add_content_lexicon(conn)


def verdict(id, verdict, lemma=None, pos=None, definition=None, example=None, reason="r"):
    return WordResolution(
        id=id,
        verdict=verdict,
        lemma=lemma,
        pos=pos,
        definition_en=definition,
        example_en=example,
        reason=reason,
    )


def scripted(*resolutions):
    """A resolver answering with the given verdicts, recording what it was asked."""

    def resolver(kind, pending):
        resolver.asked.append([p.form for p in pending])
        return Asked(list(resolutions), input_tokens=100, output_tokens=50)

    resolver.asked = []
    return resolver


def resolutions(conn):
    return [
        tuple(r)
        for r in conn.execute(
            "SELECT form, verdict, l.lemma, reviewer FROM word_resolutions "
            "LEFT JOIN lexemes AS l USING (lexeme_id) ORDER BY resolution_id"
        )
    ]


def lexeme(conn, lemma):
    return conn.execute("SELECT * FROM lexemes WHERE lemma = ?", (lemma,)).fetchone()


def test_a_variant_counts_as_the_existing_word(conn, lexicon):
    item = story(conn, "El gato fué.")
    before = conn.execute("SELECT COUNT(*) FROM lexemes").fetchone()[0]
    result = index(conn, item, resolver=scripted(verdict(1, "variant", "ser", "VERB")))
    assert vocab(conn, item) == {"el": 1, "gato": 1, "ser": 1}
    assert conn.execute("SELECT COUNT(*) FROM lexemes").fetchone()[0] == before
    assert resolutions(conn) == [("fué", "variant", "ser", "model:test")]
    assert [o.verdict for o in result.outcomes] == ["variant"]


def test_a_new_word_is_added_labeled_with_the_sentence_it_was_met_in(conn, lexicon):
    item = story(conn, "El gato chambea.")
    answer = verdict(1, "word", "Chambear", "VERB", "to work (informal)", "The cat works.")
    index(conn, item, resolver=scripted(answer))
    row = lexeme(conn, "chambear")  # normalized: lowercase
    assert row["pos"] == "VERB"
    assert (row["definition_en"], row["definition_source"]) == ("to work (informal)", "model:test")
    assert (row["example_es"], row["example_source"]) == ("El gato chambea.", f"content:{item}")
    assert (row["example_en"], row["example_en_source"]) == ("The cat works.", "model:test")
    assert row["frequency_per_million"] is None
    assert vocab(conn, item)["chambear"] == 1
    assert resolutions(conn) == [("chambea", "word", "chambear", "model:test")]


def test_not_spanish_is_excluded_and_counted_as_unresolved(conn, lexicon):
    item = story(conn, "El gato. Yeah yeah.")
    result = index(conn, item, resolver=scripted(verdict(1, "not_spanish")))
    assert vocab(conn, item) == {"el": 1, "gato": 1}
    assert result.unresolved_tokens == 2
    assert resolutions(conn) == [("yeah", "not_spanish", None, "model:test")]


@pytest.mark.parametrize(
    "answer, why",
    [
        (verdict(1, "word", "fuar", "VRB", "to fuar"), "invalid lemma or POS"),
        (verdict(1, "word", "  ", "VERB", "blank"), "invalid lemma or POS"),
        (verdict(1, "variant", "fuer", "VERB"), "a variant of no known word"),
        (verdict(1, "word", "fuar", "VERB", None), "no definition"),
    ],
)
def test_a_claim_that_fails_its_check_leaves_the_form_unresolved_and_unstored(
    conn, lexicon, answer, why
):
    item = story(conn, "Fué.")
    before = conn.execute("SELECT COUNT(*) FROM lexemes").fetchone()[0]
    result = index(conn, item, resolver=scripted(answer))
    (outcome,) = result.outcomes
    assert outcome.verdict == "rejected" and outcome.reason.startswith(why)
    assert result.unresolved_tokens == 1
    assert conn.execute("SELECT COUNT(*) FROM lexemes").fetchone()[0] == before
    assert resolutions(conn) == []  # not stored: a later run asks again


def test_an_auxiliary_answer_is_folded_into_the_verb_like_the_tagger_does(conn, lexicon):
    """The real run's "habíase" came back as haber|AUX; the pipeline has no AUX lexemes."""
    item = story(conn, "Fué.")
    index(conn, item, resolver=scripted(verdict(1, "variant", "ser", "AUX")))
    assert vocab(conn, item) == {"ser": 1}
    assert resolutions(conn) == [("fué", "variant", "ser", "model:test")]


def test_answers_to_unasked_or_repeated_numbers_are_ignored(conn, lexicon):
    item = story(conn, "Fué.")
    result = index(
        conn,
        item,
        resolver=scripted(
            verdict(7, "not_spanish"),  # nobody asked about 7
            verdict(1, "variant", "ser", "VERB"),
            verdict(1, "not_spanish"),  # a second answer to 1: the first stands
        ),
    )
    assert [o.verdict for o in result.outcomes] == ["variant"]
    assert resolutions(conn) == [("fué", "variant", "ser", "model:test")]


def test_a_form_left_unanswered_stays_unresolved(conn, lexicon):
    item = story(conn, "Fué. Yeah.")
    result = index(conn, item, resolver=scripted(verdict(2, "not_spanish")))
    assert [(o.pending.form, o.verdict) for o in result.outcomes] == [
        ("fué", "unasked"),
        ("yeah", "not_spanish"),
    ]


def test_a_new_word_that_already_exists_is_recorded_as_a_variant(conn, lexicon):
    item = story(conn, "Fué.")
    index(conn, item, resolver=scripted(verdict(1, "word", "ser", "VERB", "to be")))
    assert resolutions(conn) == [("fué", "variant", "ser", "model:test")]
    assert lexeme(conn, "ser")["definition_source"] is None  # the existing row is untouched


def test_a_variant_of_a_dictionary_word_adds_it_from_the_dictionary(conn, lexicon, make_wiktionary):
    item = story(conn, "Fué.")
    result = index_with(
        conn, item, make_wiktionary([("ir", "verb", "to go")]), verdict(1, "variant", "ir", "VERB")
    )
    assert vocab(conn, item) == {"ir": 1}
    assert lexeme(conn, "ir")["definition_source"] == "wiktionary"
    assert result.outcomes[0].verdict == "variant"


def index_with(conn, item, wiktionary, *answers):
    return index(conn, item, wiktionary, resolver=scripted(*answers))


def test_a_second_run_reuses_the_decisions_without_a_call(conn, lexicon):
    item = story(conn, "El gato chambea. Fué.")
    first = scripted(
        verdict(1, "word", "chambear", "VERB", "to work", "The cat works."),
        verdict(2, "variant", "ser", "VERB"),
    )
    index(conn, item, resolver=first)
    again = scripted()
    result = index(conn, item, resolver=again)
    assert again.asked == []
    assert result.from_cache == 2 and result.asked is None
    assert vocab(conn, item) == {"el": 1, "gato": 1, "chambear": 1, "ser": 1}


def test_two_forms_of_one_new_word_make_one_lexeme(conn, lexicon):
    item = story(conn, "Chambea. Chambeo.")
    index(
        conn,
        item,
        resolver=scripted(
            verdict(1, "word", "chambear", "VERB", "to work", "Works."),
            verdict(2, "word", "chambear", "VERB", "to work", "I work."),
        ),
    )
    assert vocab(conn, item) == {"chambear": 2}
    assert [r[1] for r in resolutions(conn)] == ["word", "variant"]


# --- The prompt and the real resolver's batching ----------------------------------------


def test_the_prompt_numbers_each_form_with_its_tagger_guess_and_sentence():
    prompt = resolution_prompt("song", [Pending("pa'", "pa'", "ADP", "Vamos pa' la playa", 2)])
    assert "1. \"pa'\" (tagger guessed pa'|ADP) in: «Vamos pa' la playa»" in prompt
    assert "song" in prompt and "EXPR" in prompt


class FakeGenerator:
    def __init__(self):
        self.prompts = []

    def ask(self, schema, prompt):
        from spanish_tutor.conversation import Generation

        assert schema is Resolutions
        self.prompts.append(prompt)
        count = prompt.count(" (tagger guessed ")
        return Generation(
            Resolutions(resolutions=[verdict(i, "not_spanish") for i in range(1, count + 1)]),
            input_tokens=10,
            cache_read_tokens=5,
            output_tokens=3,
        )


def test_large_items_are_resolved_in_numbered_chunks():
    resolver = ClaudeResolver.__new__(ClaudeResolver)  # no API client
    resolver.model, resolver.generator = "claude-test", FakeGenerator()
    pending = [Pending(f"w{i}", f"w{i}", "NOUN", "x", 1) for i in range(MAX_FORMS_PER_CALL + 5)]
    asked = resolver(pending=pending, kind="story")
    assert len(resolver.generator.prompts) == 2
    assert [r.id for r in asked.resolutions] == list(range(1, len(pending) + 1))
    assert (asked.input_tokens, asked.output_tokens) == (30, 6)
    assert resolver.reviewer == "model:claude-test"
