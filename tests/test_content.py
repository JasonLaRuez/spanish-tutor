"""Content items and the difficulty index (content.py), with a fake analyzer: no spaCy."""

import pytest
from fakes import index_content as index
from fakes import story, vocab

from spanish_tutor import content
from spanish_tutor.content import add_book, add_item, sentences, stale_items
from spanish_tutor.resolve import Asked


@pytest.fixture
def lexicon(conn):
    from fakes import add_content_lexicon

    return add_content_lexicon(conn)


def never_called(kind, pending):
    raise AssertionError(f"the resolver was called for {[p.form for p in pending]}")


# --- Splitting and counting -------------------------------------------------------------


def test_songs_split_into_lines_and_prose_into_sentences():
    assert sentences("Ay, mi amor\n\nte quiero\n", "song") == ["Ay, mi amor", "te quiero"]
    prose = "El gato come. ¿Duerme? ¡Sí!\n\nOtro párrafo sin punto"
    assert sentences(prose, "story") == [
        "El gato come.",
        "¿Duerme?",
        "¡Sí!",
        "Otro párrafo sin punto",
    ]


def test_hard_wrapped_prose_is_rejoined_into_sentences():
    """Gutenberg wraps lines at ~70 characters: a line break inside a paragraph is a space."""
    wrapped = "como ella que lo mejor era separarnos y le jura no\nquererla más. Se fue.\n\nFin"
    assert sentences(wrapped, "story") == [
        "como ella que lo mejor era separarnos y le jura no quererla más.",
        "Se fue.",
        "Fin",
    ]


def test_an_opening_mark_after_final_punctuation_starts_a_sentence():
    assert sentences("Una queja...¿no? Cualquier cosa...¡pero rabioso!", "story") == [
        "Una queja...",
        "¿no?",
        "Cualquier cosa...",
        "¡pero rabioso!",
    ]


def test_every_occurrence_is_counted(conn, lexicon):
    item = story(conn, "El gato come. El gato duerme.")
    result = index(conn, item)
    assert vocab(conn, item) == {"el": 2, "gato": 2, "comer": 1, "dormir": 1}
    assert (result.tokens, result.unresolved_tokens, result.words) == (6, 0, 4)


def test_a_contraction_counts_both_words(conn, lexicon):
    item = story(conn, "Sale del jardín.")
    index(conn, item)
    assert vocab(conn, item) == {"salir": 1, "de": 1, "el": 1, "jardín": 1}


def test_an_expression_counts_once_and_not_as_its_parts(conn, lexicon):
    item = story(conn, "Sin embargo, llueve.")
    result = index(conn, item)
    assert vocab(conn, item) == {"sin embargo": 1, "llover": 1}
    assert result.tokens == 2


def test_names_are_not_vocabulary(conn, lexicon):
    item = story(conn, "María come.")
    assert (index(conn, item).tokens, vocab(conn, item)) == (1, {"comer": 1})


def test_a_dictionary_word_missing_from_the_lexicon_is_added(conn, lexicon, make_wiktionary):
    item = story(conn, "El gato nada.")
    result = index(conn, item, make_wiktionary([("nadar", "verb", "to swim")]))
    assert vocab(conn, item) == {"el": 1, "gato": 1, "nadar": 1}
    assert result.from_dictionary == 1
    row = conn.execute("SELECT definition_source FROM lexemes WHERE lemma = 'nadar'").fetchone()
    assert row[0] == "wiktionary"


# --- Forms nobody knows -----------------------------------------------------------------


def test_without_a_resolver_unknown_forms_are_unresolved_and_not_added(conn, lexicon):
    item = story(conn, "El gato fué. Yeah yeah.")
    before = conn.execute("SELECT COUNT(*) FROM lexemes").fetchone()[0]
    result = index(conn, item)
    assert (result.tokens, result.unresolved_tokens) == (2, 3)
    assert [(o.pending.form, o.pending.occurrences, o.verdict) for o in result.outcomes] == [
        ("yeah", 2, "unasked"),  # most frequent first
        ("fué", 1, "unasked"),
    ]
    assert result.outcomes[1].pending.context == "El gato fué."
    assert conn.execute("SELECT COUNT(*) FROM lexemes").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM word_resolutions").fetchone()[0] == 0


def add_resolution(conn, form, lemma, verdict, lexeme_id, pos="VERB"):
    conn.execute(
        "INSERT INTO word_resolutions (form, tagged_lemma, tagged_pos, verdict, lexeme_id, "
        "reviewer) VALUES (?, ?, ?, ?, ?, 'human')",
        (form, lemma, pos, verdict, lexeme_id),
    )


def test_earlier_decisions_are_reused_without_a_call(conn, lexicon):
    add_resolution(conn, "fué", "fuar", "variant", lexicon["ser"])
    add_resolution(conn, "yeah", "yeah", "not_spanish", None, pos="NOUN")
    item = story(conn, "El gato fué. Yeah.")
    result = index(conn, item, resolver=never_called)
    assert vocab(conn, item) == {"el": 1, "gato": 1, "ser": 1}
    assert (result.unresolved_tokens, result.from_cache, result.asked) == (1, 2, None)


def test_the_latest_decision_for_a_form_wins(conn, lexicon):
    add_resolution(conn, "fué", "fuar", "not_spanish", None)
    add_resolution(conn, "fué", "fuar", "variant", lexicon["ser"])  # corrected later
    item = story(conn, "Fué.")
    index(conn, item, resolver=never_called)
    assert vocab(conn, item) == {"ser": 1}


def test_a_failed_model_call_writes_nothing(conn, lexicon, make_wiktionary):
    item = story(conn, "El gato nada. Fué.")
    conn.commit()
    lexemes_before = conn.execute("SELECT COUNT(*) FROM lexemes").fetchone()[0]

    def failing(kind, pending):
        raise TimeoutError("API down")

    with pytest.raises(TimeoutError):
        index(conn, item, make_wiktionary([("nadar", "verb", "to swim")]), resolver=failing)
    assert conn.execute("SELECT COUNT(*) FROM lexemes").fetchone()[0] == lexemes_before
    assert vocab(conn, item) == {}
    assert stale_items(conn, "setup-1") == [item]


def test_the_resolver_sees_the_item_kind_and_each_pending_form_once(conn, lexicon):
    seen = []

    def recording(kind, pending):
        seen.append((kind, [(p.form, p.lemma, p.pos, p.occurrences) for p in pending]))
        return Asked([])

    song = add_item(conn, "song", "Canción", "Fué\nfué yeah", source="private", is_private=True)
    index(conn, song, resolver=recording)
    assert seen == [("song", [("fué", "fuar", "VERB", 2), ("yeah", "yeah", "NOUN", 1)])]


# --- Re-indexing ------------------------------------------------------------------------


def test_reindexing_replaces_the_items_rows_and_records_the_setup(conn, lexicon):
    item = story(conn, "El gato come.")
    other = story(conn, "El gato duerme.")
    index(conn, item)
    index(conn, other)
    conn.execute("UPDATE content_items SET text_es = 'Llueve.' WHERE content_id = ?", (item,))
    index(conn, item, analyzer="setup-2")
    assert vocab(conn, item) == {"llover": 1}
    assert vocab(conn, other) == {"el": 1, "gato": 1, "dormir": 1}  # untouched
    first = vocab(conn, item)
    index(conn, item, analyzer="setup-2")
    assert vocab(conn, item) == first  # idempotent
    analyzer = conn.execute(
        "SELECT analyzer FROM content_items WHERE content_id = ?", (item,)
    ).fetchone()[0]
    assert analyzer == "setup-2"


def test_stale_items_are_unindexed_or_indexed_under_another_setup(conn, lexicon):
    current, old, never = (story(conn, t) for t in ("El gato.", "Come.", "Duerme."))
    index(conn, current, analyzer="setup-2")
    index(conn, old, analyzer="setup-1")
    assert stale_items(conn, "setup-2") == [old, never]


# --- Adding content and reading it ------------------------------------------------------


def test_a_book_is_added_with_its_chapters_in_order(conn):
    book, ids = add_book(
        conn, "Cuentos", [("I", "Uno."), ("II", "Dos.")], source="gutenberg:2", is_private=False
    )
    rows = conn.execute(
        "SELECT content_id, chapter_no, title, source FROM content_items WHERE book_id = ? "
        "ORDER BY chapter_no",
        (book,),
    ).fetchall()
    assert [tuple(r) for r in rows] == [(ids[0], 1, "I", None), (ids[1], 2, "II", None)]


def test_add_item_does_not_add_chapters(conn):
    with pytest.raises(ValueError, match="add_book"):
        add_item(conn, "chapter", "I", "Uno.", source="x", is_private=False)


def test_starting_and_finishing_are_logged(conn):
    song = add_item(conn, "song", "Canción", "La la.", source="private", is_private=True)
    content.start(conn, song, "requested")
    content.finish(conn, song)
    rows = conn.execute("SELECT event, chosen_via FROM content_events ORDER BY event_id")
    assert [tuple(r) for r in rows] == [("started", "requested"), ("finished", None)]


def test_cost_estimate_grows_with_forms_and_is_zero_without_any():
    assert content.estimate_cost(0) == 0
    assert 0 < content.estimate_cost(10) < content.estimate_cost(100)


def test_chapter_titles_drop_the_number_that_orders_the_files():
    from pathlib import Path

    assert content.chapter_title(Path("03 El solitario.txt")) == "El solitario"
    assert content.chapter_title(Path("12_Yaguaí.txt")) == "Yaguaí"
    assert content.chapter_title(Path("Prólogo.txt")) == "Prólogo"
    assert content.chapter_title(Path("2001.txt")) == "2001"
