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


# --- Batch resolution (index --batch) ------------------------------------------------------


def analyzed(conn, content_id):
    from fakes import analyze_many

    kind, text = content.item_kind(conn, content_id)
    return content_id, kind, content.analyze_text(text, kind, analyze_many)


def test_a_batch_asks_about_each_form_once_per_kind_with_occurrences_summed(conn, lexicon):
    from spanish_tutor.words import LexiconIndex

    first = story(conn, "Fué. Yeah.")
    second = story(conn, "Fué fué.")
    poem = add_item(conn, "poem", "Rima", "Fué", source="g", is_private=False)
    plan = content.plan_batch(
        conn, LexiconIndex(conn), [analyzed(conn, i) for i in (first, second, poem)]
    )
    assert {k: [(p.form, p.occurrences) for p in v] for k, v in plan.by_kind.items()} == {
        "story": [("fué", 3), ("yeah", 1)],
        "poem": [("fué", 1)],
    }
    assert plan.forms == 3
    key = ("fué", "fuar", "VERB")
    assert (plan.first_item["story", key], plan.first_item["poem", key]) == (first, poem)


def test_batch_decisions_are_stored_and_found_when_each_item_is_indexed(conn, lexicon):
    from fakes import analyze_many

    from spanish_tutor.resolve import WordResolution
    from spanish_tutor.words import LexiconIndex

    first, second = story(conn, "El gato fué."), story(conn, "Fué. Yeah.")
    asked = []

    def resolver(kind, pending):
        asked.append([p.form for p in pending])
        return Asked(
            [
                WordResolution(
                    id=1,
                    verdict="variant",
                    lemma="ser",
                    pos="VERB",
                    definition_en=None,
                    example_en=None,
                    reason="1952 spelling",
                ),
                WordResolution(
                    id=2,
                    verdict="not_spanish",
                    lemma=None,
                    pos=None,
                    definition_en=None,
                    example_en=None,
                    reason="English",
                ),
            ],
            100,
            50,
        )

    index_ = LexiconIndex(conn)
    items = [analyzed(conn, i) for i in (first, second)]
    plan = content.plan_batch(conn, index_, items)
    outcomes, calls = content.resolve_batch(conn, index_, plan, resolver, "model:test")
    assert asked == [["fué", "yeah"]]  # one request for both items
    assert [(cid, o.verdict) for cid, o in outcomes] == [
        (first, "variant"),
        (second, "not_spanish"),
    ]
    assert len(calls) == 1
    for content_id, _, a in items:
        result = content.index_item(conn, index_, content_id, analyze_many, "setup-1", analyzed=a)
        assert result.asked is None
    assert vocab(conn, second) == {"ser": 1}
    assert (
        conn.execute(
            "SELECT unresolved_tokens FROM content_items WHERE content_id = ?", (second,)
        ).fetchone()[0]
        == 1
    )


def test_a_batch_form_left_unanswered_stays_pending_for_a_later_run(conn, lexicon):
    from spanish_tutor.words import LexiconIndex

    item = story(conn, "Fué.")
    index_ = LexiconIndex(conn)
    plan = content.plan_batch(conn, index_, [analyzed(conn, item)])
    outcomes, _ = content.resolve_batch(
        conn, index_, plan, lambda kind, pending: Asked([]), "model:test"
    )
    assert [o.verdict for _, o in outcomes] == ["unasked"]
    assert conn.execute("SELECT COUNT(*) FROM word_resolutions").fetchone()[0] == 0
    assert content.plan_batch(conn, index_, [analyzed(conn, item)]).forms == 1


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


def test_a_collection_groups_items_in_the_catalog_in_the_order_they_were_added(conn):
    from spanish_tutor import progress

    loose = add_item(conn, "story", "Aaa", "Uno.", source="x", is_private=False)
    second = add_item(
        conn, "poem", "Rima II", "Dos.", source="g", is_private=False, collection="Rimas"
    )
    first = add_item(
        conn, "poem", "Rima X", "Diez.", source="g", is_private=False, collection="Rimas"
    )
    rows = progress.rows(conn, "content_catalog")
    assert [(r["content_id"], r["collection"]) for r in rows] == [
        (second, "Rimas"),
        (first, "Rimas"),  # added order, not alphabetical: a collection's own order
        (loose, None),
    ]


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


def test_a_poem_is_verse_analyzed_line_by_line_in_stanzas(conn):
    poem = add_item(
        conn,
        "poem",
        "Rima XXIII",
        "Por una mirada, un mundo;\n    Por una sonrisa, un cielo;\n\nPor un beso... ¡yo no sé",
        source="gutenberg:53552",
        is_private=False,
        author="Bécquer",
    )
    assert (
        conn.execute("SELECT kind FROM content_items WHERE content_id = ?", (poem,)).fetchone()[0]
        == "poem"
    )
    assert content.paragraphs("Uno, dos\n  tres\n\ncuatro", "poem") == [
        ["Uno, dos", "tres"],
        ["cuatro"],
    ]
    assert content.sentences("Uno, dos\n  tres", "poem") == ["Uno, dos", "tres"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Dile - Don Omar.txt", ("Dile", ["Don Omar"])),
        ("Chantaje - Shakira, Maluma.txt", ("Chantaje", ["Shakira", "Maluma"])),
        ("Uno - Dos - Artista.txt", ("Uno - Dos", ["Artista"])),  # the last " - " splits
        ("Si No Te Quiere.txt", None),  # no artist
        (" - Artista.txt", None),  # no title
    ],
)
def test_a_lyrics_file_name_gives_the_title_and_artists(name, expected):
    from pathlib import Path

    assert content.song_name(Path(name)) == expected


def test_a_folder_of_lyrics_is_added_once_as_private_songs(conn, tmp_path):
    folder = tmp_path / "lyrics"
    folder.mkdir()
    (folder / "Chantaje - Shakira, Maluma.txt").write_text(
        "\ufeffLínea uno\nLínea dos\n", encoding="utf-8"
    )
    (folder / "Dile - Don Omar.txt").write_text("Dile\n", encoding="utf-8")
    (folder / "Sin artista.txt").write_text("Hola\n", encoding="utf-8")

    first = content.add_songs(conn, folder)
    assert [name for _, name in first.added] == [
        "Chantaje - Shakira, Maluma.txt",
        "Dile - Don Omar.txt",
    ]
    assert first.unnamed == ["Sin artista.txt"]
    rows = conn.execute(
        "SELECT title, author, collection, is_private, source, text_es FROM content_items ORDER BY content_id"
    ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("Chantaje", "Shakira, Maluma", None, 1, "private", "Línea uno\nLínea dos"),  # no BOM
        ("Dile", "Don Omar", None, 1, "private", "Dile"),
    ]

    (folder / "Nueva - Ozuna.txt").write_text("Nueva\n", encoding="utf-8")
    again = content.add_songs(conn, folder)  # the folder grows: only the new song is added
    assert ([name for _, name in again.added], len(again.already)) == (["Nueva - Ozuna.txt"], 2)
