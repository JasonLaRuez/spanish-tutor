"""English inside songs: marking, storing, and every step that skips English words."""

import pytest
from fakes import Scripted, add_content_lexicon, analyze_content, analyze_many

from spanish_tutor import content, english
from spanish_tutor.content import add_item
from spanish_tutor.conversation import Generation
from spanish_tutor.english import EnglishLine, EnglishWords, is_english
from spanish_tutor.lyrics import translation_prompt
from spanish_tutor.reading import ReadingSession
from spanish_tutor.words import LexiconIndex

SONG = "El gato come.\nSo come with me\n\nLa party sale del jardín."
# Line 2 switches into English (its "come" is not comer); line 3's party is a loanword.
MARKS = {2: {"so": "english", "come": "english", "with": "english", "me": "english"},
         3: {"party": "loanword"}}  # fmt: skip


def answer(*lines):
    """A fake ClaudeGenerator.ask answering with the given lines, recording its prompts."""
    prompts = []

    def ask(schema, prompt):
        assert schema is EnglishWords
        prompts.append(prompt)
        return Generation(EnglishWords(lines=list(lines)), input_tokens=500, output_tokens=60)

    ask.prompts = prompts
    return ask


@pytest.fixture
def lexicon(conn):
    ids = add_content_lexicon(conn)
    conn.execute(
        "INSERT INTO word_events (lexeme_id, mode, event_type, source) "
        "VALUES (?, 'recognition', 'taught', 'seed')",
        (ids["el"],),
    )
    return ids


def song(conn, text=SONG, private=True):
    return add_item(conn, "song", "Canción", text, source="private", is_private=private)


# --- Marking --------------------------------------------------------------------------------


def test_marking_keeps_only_words_on_their_line_and_lines_that_exist():
    lines = ["El gato come.", "So come with me", "La party sale del jardín."]
    ask = answer(
        EnglishLine(line_no=2, english=["So", "come", "with", "me", "baby"], loanwords=[]),
        EnglishLine(line_no=3, english=[], loanwords=["party"]),
        EnglishLine(line_no=9, english=["ghost"], loanwords=[]),
    )
    marks, generation = english.mark("Canción", lines, ask)
    assert marks == MARKS  # "baby" isn't on line 2, line 9 doesn't exist
    assert generation.input_tokens == 500
    assert "2. So come with me" in ask.prompts[0] and "«Canción»" in ask.prompts[0]


def test_a_word_given_as_both_english_and_loanword_counts_as_english():
    ask = answer(EnglishLine(line_no=1, english=["party"], loanwords=["party"]))
    marks, _ = english.mark("x", ["Let's party"], ask)
    assert marks == {1: {"party": "english"}}


def test_marks_are_stored_once_per_song_and_loaded_by_kind(conn, lexicon):
    item = song(conn)
    assert not english.checked(conn, item)
    english.store(conn, item, MARKS, "model:test")
    assert english.checked(conn, item)
    assert english.load_marks(conn, item) == MARKS
    assert english.load(conn, item) == {2: {"so", "come", "with", "me"}}  # not loanwords
    english.store(conn, item, {}, "model:test")  # a re-check replaces the marks
    assert english.load_marks(conn, item) == {} and english.checked(conn, item)


@pytest.mark.parametrize(
    ("form", "words", "expected"),
    [
        ("come", {"so", "come"}, True),
        ("comer", {"so", "come"}, False),
        ("do", {"don't"}, True),  # the tagger splits don't into do + n't
        ("n't", {"don't"}, True),
        ("'m", {"i'm"}, True),
        ("i", {"i'm"}, True),
        ("love you", {"love", "you"}, True),  # a multi-word token: every word English
        ("me gusta", {"me"}, False),
        ("come", None, False),
    ],
)
def test_a_token_is_english_when_each_of_its_words_is(form, words, expected):
    assert is_english(form, words) is expected


# --- Indexing -------------------------------------------------------------------------------


def test_indexing_skips_a_lines_english_words_but_counts_loanwords(conn, lexicon):
    english_words = {2: {"so", "come", "with", "me"}}
    counts = content.analyze_text(SONG, "song", analyze_many, english_words).counts
    assert counts[("come", "comer", "VERB")] == 1  # line 1's come only
    assert not any(form in ("so", "with", "me") for form, _, _ in counts)
    assert counts[("party", "party", "NOUN")] == 1  # a loanword is Spanish vocabulary


def test_an_item_is_indexed_with_its_stored_english(conn, lexicon):
    from fakes import index_content, vocab

    item = song(conn)
    english.store(conn, item, MARKS, "model:test")
    index_content(conn, item)
    assert vocab(conn, item)["comer"] == 1


def test_only_unchecked_songs_are_checked(conn, lexicon):
    first, second = song(conn), song(conn, "Hola")
    poem = add_item(conn, "poem", "Rima", "So come", source="g", is_private=False)
    story = add_item(conn, "story", "Cuento", "So come", source="g", is_private=False)
    english.store(conn, second, {}, "model:test")
    assert content.unchecked_songs(conn, [first, second, poem, story]) == [first]
    ask = answer(EnglishLine(line_no=2, english=["so", "come", "with", "me"], loanwords=[]))
    assert content.check_english(conn, [first], ask, "model:test") == (500, 60)
    assert english.load(conn, first) == {2: {"so", "come", "with", "me"}}
    assert content.english_estimate(conn, [first]) == pytest.approx(english.estimate_cost(3))


# --- Reading and lyrics ---------------------------------------------------------------------


def reading(conn, item):
    conn.commit()
    return ReadingSession(conn, Scripted(), LexiconIndex(conn), analyze_content, item, "requested")


def test_english_words_are_never_new_words_or_looked_up(conn, lexicon):
    conn.execute("INSERT INTO lexemes (lemma, pos) VALUES ('party', 'NOUN')")  # a loanword
    item = song(conn)
    english.store(conn, item, MARKS, "model:test")
    session = reading(conn, item)
    new = [(w.lexeme.lemma, w.sentence) for w in session.new_words]
    assert ("comer", 0) in new and not any(lemma in ("so", "with", "me") for lemma, _ in new)
    assert "party" in session.unstudied_forms()  # loanwords are taught like any word
    assert not {"so", "with"} & session.unstudied_forms()
    assert session.only_english("with") and session.only_english("Me")
    assert not session.only_english("come")  # Spanish on line 1
    assert session.look_up("with") is None
    assert session.marked_words() == [{}, MARKS[2], MARKS[3]]


def test_an_unchecked_song_reads_as_before(conn, lexicon):
    session = reading(conn, song(conn))
    assert session.marked_words() == [{}, {}, {}]
    assert not session.only_english("with")


def test_the_translation_keeps_english_as_written():
    prompt = translation_prompt(
        "song", "Canción", ["El gato come.", "So come with me"], [], {2: {"come", "so"}}
    )
    assert "Keep these words as written" in prompt and "- line 2: come, so" in prompt
    assert "Keep these words" not in translation_prompt("song", "Canción", ["El gato come."], [])
