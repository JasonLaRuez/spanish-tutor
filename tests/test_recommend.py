"""The recommender's ranking queries (sql/queries/recommend_*.sql), against hand-built
difficulty-index rows: no analyzer involved, so each test states exactly what an item
contains."""

import random

import pytest

from spanish_tutor import content, recommend


@pytest.fixture
def words(conn):
    """60 lexemes, w0..w59: lemma -> lexeme_id."""
    return {
        f"w{i}": conn.execute(
            "INSERT INTO lexemes (lemma, pos) VALUES (?, 'NOUN')", (f"w{i}",)
        ).lastrowid
        for i in range(60)
    }


def know(conn, *lexeme_ids, mode="recognition"):
    event = "taught" if mode == "recognition" else "used"
    conn.executemany(
        "INSERT INTO word_events (lexeme_id, mode, event_type, source) VALUES (?, ?, ?, 'seed')",
        [(i, mode, event) for i in lexeme_ids],
    )


def set_vocab(conn, content_id, vocab):
    """Index an item by hand: vocab maps lexeme_id -> occurrences."""
    conn.executemany(
        "INSERT INTO content_vocab VALUES (?, ?, ?)",
        [(content_id, lexeme_id, n) for lexeme_id, n in vocab.items()],
    )
    conn.execute(
        "UPDATE content_items SET tokens = ?, unresolved_tokens = 0, analyzer = 't', "
        "indexed_at = CURRENT_TIMESTAMP WHERE content_id = ?",
        (sum(vocab.values()), content_id),
    )


def item(conn, title, vocab, kind="song"):
    content_id = content.add_item(conn, kind, title, "...", source="private", is_private=True)
    set_vocab(conn, content_id, vocab)
    return content_id


def book(conn, title, chapter_vocabs):
    book_id, ids = content.add_book(
        conn,
        title,
        [(f"{title} {n}", "...") for n in range(1, len(chapter_vocabs) + 1)],
        source="gutenberg:1",
        is_private=False,
    )
    for content_id, vocab in zip(ids, chapter_vocabs, strict=True):
        if vocab is not None:
            set_vocab(conn, content_id, vocab)
    return book_id, ids


def titles(rows):
    return [r["title"] for r in rows]


# --- Songs and stories ------------------------------------------------------------------


def test_new_words_and_coverage_match_a_direct_computation_on_random_data(conn, words):
    """The roadmap's check: the SQL agrees with plain set arithmetic, and the default
    suggestion really has the fewest new words."""
    rng = random.Random(7)
    ids = list(words.values())
    known = set(rng.sample(ids, 25))
    know(conn, *known)
    know(conn, *rng.sample(ids, 10), mode="production")  # production alone is not "known"
    expected = {}
    for n in range(30):
        vocab = {i: rng.randint(1, 9) for i in rng.sample(ids, rng.randint(1, 20))}
        title = f"item {n:02}"
        item(conn, title, vocab, kind=rng.choice(["song", "story"]))
        new = {i for i in vocab if i not in known}
        tokens = sum(vocab.values())
        expected[title] = (len(new), 1 - sum(vocab[i] for i in new) / tokens)

    ranked = recommend.items(conn, limit=100)

    assert len(ranked) == 30
    for row in ranked:
        new_words, coverage = expected[row["title"]]
        assert row["new_words"] == new_words
        assert row["coverage"] == pytest.approx(coverage)
    assert titles(ranked) == sorted(expected, key=lambda t: (expected[t][0], -expected[t][1], t))
    assert ranked[0]["new_words"] == min(new for new, _ in expected.values())


def test_ties_on_new_words_go_to_the_better_covered_item(conn, words):
    w = words
    know(conn, w["w0"])
    item(conn, "rare", {w["w0"]: 1, w["w1"]: 1})  # coverage 50%
    item(conn, "common", {w["w0"]: 9, w["w1"]: 1})  # coverage 90%
    assert titles(recommend.items(conn)) == ["common", "rare"]


def test_only_recognized_words_count_as_known(conn, words):
    w = words
    know(conn, w["w0"], mode="production")  # used, but never taught for recognition
    item(conn, "a", {w["w0"]: 1})
    assert recommend.items(conn)[0]["new_words"] == 1


def test_finished_unindexed_and_chapters_are_left_out(conn, words):
    w = words
    done = item(conn, "done", {w["w0"]: 1})
    item(conn, "next", {w["w1"]: 1})
    content.add_item(conn, "story", "not indexed", "...", source="x", is_private=False)
    book(conn, "Libro", [{w["w2"]: 1}])
    content.start(conn, done, "recommended")
    assert titles(recommend.items(conn)) == ["done", "next"]  # started is still eligible
    content.finish(conn, done)
    assert titles(recommend.items(conn)) == ["next"]


def test_the_limit_caps_the_list(conn, words):
    for n in range(5):
        item(conn, f"i{n}", {words[f"w{n}"]: 1})
    assert len(recommend.items(conn, limit=3)) == 3


def test_an_items_new_words_come_most_frequent_first(conn, words):
    w = words
    know(conn, w["w0"])
    conn.execute("UPDATE lexemes SET frequency_per_million = 50 WHERE lemma = 'w3'")
    conn.execute("UPDATE lexemes SET definition_source = 'model:x' WHERE lemma IN ('w1', 'w3')")
    song = item(conn, "s", {w["w0"]: 5, w["w1"]: 1, w["w2"]: 4, w["w3"]: 1})
    found = recommend.new_words(conn, song)
    assert [(r["lemma"], r["occurrences"]) for r in found] == [("w2", 4), ("w3", 1), ("w1", 1)]
    assert [r["model_written"] for r in found] == [False, True, True]


# --- Books ------------------------------------------------------------------------------


def test_a_short_easy_preface_does_not_make_a_hard_book_look_easy(conn, words):
    """Jason's case: chapter 1 is tiny and fully known, the rest are dense with new words.
    Ranked by the first chapter it would win; by the whole book it loses."""
    w = words
    know(conn, *[w[f"w{i}"] for i in range(10)])
    known = {w[f"w{i}"]: 10 for i in range(10)}
    hard_rest = {w[f"w{i}"]: 3 for i in range(20, 40)}
    book(conn, "Preface", [{w["w0"]: 2}, hard_rest, hard_rest])
    moderate = known | {w["w50"]: 5}  # 5 unknown running words in 105, each chapter
    book(conn, "Steady", [moderate, moderate, moderate])

    ranked = recommend.books(conn)

    assert titles(ranked) == ["Steady", "Preface"]
    preface = ranked[1]
    assert preface["density"] == pytest.approx(120 / 122)  # 2 known + 2 x 60 unknown
    assert (preface["next_chapter_no"], preface["next_chapter_new_words"]) == (1, 0)
    assert ranked[0]["new_words"] == 1  # w50 in every chapter is one word to learn


def test_a_started_book_comes_before_an_easier_new_one(conn, words):
    w = words
    know(conn, w["w0"])
    _, hard = book(conn, "Hard", [{w["w1"]: 5}, {w["w2"]: 5}])
    book(conn, "Easy", [{w["w0"]: 5}, {w["w0"]: 5}])
    assert titles(recommend.books(conn)) == ["Easy", "Hard"]
    content.start(conn, hard[0], "requested")
    ranked = recommend.books(conn)
    assert titles(ranked) == ["Hard", "Easy"]
    assert (ranked[0]["state"], ranked[0]["next_chapter_no"]) == ("in progress", 1)
    assert ranked[1]["state"] == "new"


def test_chapters_come_in_order_and_finishing_one_moves_to_the_next(conn, words):
    vocabs = [{words[f"w{n}"]: 1} for n in range(8)]
    _, ids = book(conn, "Libro", vocabs)
    for chapter in range(6):  # read chapters 1-6
        content.start(conn, ids[chapter], "recommended")
        assert recommend.books(conn)[0]["next_chapter_no"] == chapter + 1
        content.finish(conn, ids[chapter])
    assert recommend.books(conn)[0]["next_chapter_no"] == 7


def test_a_chapter_read_on_request_moves_the_pointer_past_it(conn, words):
    _, ids = book(conn, "Libro", [{words[f"w{n}"]: 1} for n in range(9)])
    content.start(conn, ids[6], "requested")  # jumped to chapter 7
    assert recommend.books(conn)[0]["next_chapter_no"] == 1  # only started, not finished
    content.finish(conn, ids[6])
    assert recommend.books(conn)[0]["next_chapter_no"] == 8


def test_a_finished_book_is_left_out(conn, words):
    _, ids = book(conn, "Corto", [{words["w0"]: 1}, {words["w1"]: 1}])
    for content_id in ids:
        content.finish(conn, content_id)
    assert recommend.books(conn) == []


def test_a_book_not_fully_indexed_is_left_out(conn, words):
    book(conn, "Half", [{words["w0"]: 1}, None])  # chapter 2 not indexed
    book(conn, "Whole", [{words["w0"]: 1}])
    assert titles(recommend.books(conn)) == ["Whole"]


def test_with_two_started_books_the_most_recently_read_leads(conn, words):
    _, a = book(conn, "A", [{words["w0"]: 1}, {words["w1"]: 1}])
    _, b = book(conn, "B", [{words["w2"]: 1}, {words["w3"]: 1}])
    content.start(conn, a[0], "recommended")
    content.start(conn, b[0], "requested")  # same second as A: event order decides
    assert titles(recommend.books(conn)) == ["B", "A"]
    content.finish(conn, a[0])
    assert titles(recommend.books(conn)) == ["A", "B"]


# --- Surprise me, the catalog, and one item -------------------------------------------


def test_a_surprise_comes_from_the_easiest_items_or_a_started_books_next_chapter(conn, words):
    w = words
    for n in range(8):  # item n has n new words
        item(conn, f"song {n}", {w[f"w{i}"]: 1 for i in range(10, 10 + n)} | {w["w0"]: 1})
    know(conn, w["w0"])
    _, started = book(conn, "Started", [{w["w1"]: 1}, {w["w2"]: 1}])
    book(conn, "Unstarted", [{w["w0"]: 1}])  # easy, but never a surprise: not begun
    content.start(conn, started[0], "requested")
    content.finish(conn, started[0])

    picks = {recommend.surprise(conn, random.Random(seed))["title"] for seed in range(200)}

    assert picks == {f"song {n}" for n in range(5)} | {"Started, Started 2"}


def test_a_surprise_with_nothing_to_choose_from_is_none(conn):
    assert recommend.surprise(conn) is None


def test_the_catalog_lists_everything_with_its_reading_state(conn, words):
    w = words
    know(conn, w["w0"])
    song = item(conn, "Una canción", {w["w0"]: 2, w["w1"]: 1})
    _, chapters = book(conn, "Libro", [{w["w2"]: 1}, None])
    content.start(conn, song, "recommended")
    content.finish(conn, song)
    content.start(conn, song, "requested")  # opened again: still finished
    content.start(conn, chapters[0], "recommended")

    rows = {r["title"]: r for r in recommend.catalog(conn)}

    assert [r["title"] for r in recommend.catalog(conn)] == ["Una canción", "Libro 1", "Libro 2"]
    assert (rows["Una canción"]["new_words"], rows["Una canción"]["state"]) == (1, "finished")
    assert rows["Una canción"]["coverage"] == pytest.approx(2 / 3)
    assert (rows["Libro 1"]["book_title"], rows["Libro 1"]["state"]) == ("Libro", "started")
    assert rows["Libro 2"]["indexed"] is False and rows["Libro 2"]["new_words"] is None
    assert rows["Libro 2"]["state"] is None


def test_one_item_has_its_text_book_position_and_state(conn, words):
    _, chapters = book(conn, "Libro", [{words["w0"]: 1}, {words["w1"]: 1}])
    content.start(conn, chapters[1], "requested")
    detail = recommend.item(conn, chapters[1])
    assert (detail["book_title"], detail["chapter_no"], detail["chapters"]) == ("Libro", 2, 2)
    assert (detail["author"], detail["source"], detail["text_es"]) == (None, "gutenberg:1", "...")
    assert (detail["started"], detail["finished"], detail["indexed"]) == (True, False, True)
    assert recommend.item(conn, 999) is None
