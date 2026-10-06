"""The lyrics skill (lyrics.py), with the content fakes and scripted models: no spaCy, no
API calls."""

import pytest
from fakes import Scripted, add_content_lexicon, analyze_content

from spanish_tutor.content import add_item
from spanish_tutor.conversation import Generation
from spanish_tutor.lyrics import (
    AttemptFeedback,
    LineFeedback,
    LineTranslation,
    LyricsSession,
    SongTranslation,
    TranslationError,
    stored_translation,
)
from spanish_tutor.words import LexiconIndex

POEM = "El gato come.\nSin embargo, María duerme.\n\nEl gato sale del jardín."


@pytest.fixture
def lexicon(conn):
    ids = add_content_lexicon(conn)
    conn.execute(
        "UPDATE lexemes SET definition_en = 'however, nevertheless', "
        "example_es = 'Sin embargo, no llueve.', example_en = 'However, it is not raining.' "
        "WHERE lexeme_id = ?",
        (ids["sin embargo"],),
    )
    conn.executemany(
        "INSERT INTO word_events (lexeme_id, mode, event_type, source) "
        "VALUES (?, 'recognition', 'taught', 'seed')",
        [(ids["el"],), (ids["gato"],), (ids["de"],)],
    )
    return ids


def lines(*numbers):
    return SongTranslation(
        lines=[
            LineTranslation(
                line_no=n,
                natural_en=f"natural {n}",
                literal_en=f"literal {n}",
                note_en="figurative" if n == 2 else None,
            )
            for n in numbers
        ]
    )


class Translator:
    """The translation model, scripted: each call returns the next answer."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, schema, prompt):
        assert schema is SongTranslation
        self.prompts.append(prompt)
        return Generation(self.answers.pop(0), input_tokens=900, output_tokens=400)


class Commenter(Scripted):
    """The session's generator, with `ask` for the comparison."""

    def ask(self, schema, prompt):
        assert schema is AttemptFeedback
        self.requests.append(prompt)
        return Generation(self.replies.pop(0), input_tokens=300, output_tokens=120)


def session(conn, translate, generate=None, text=POEM):
    poem = add_item(
        conn, "poem", "Rima", text, source="gutenberg:53552", is_private=False, author="Bécquer"
    )
    conn.commit()
    return LyricsSession(
        conn,
        generate or Commenter(),
        LexiconIndex(conn),
        analyze_content,
        poem,
        "requested",
        translate=translate,
    )


def test_a_lyrics_session_is_a_reading_session_with_its_own_skill(conn, lexicon):
    song = session(conn, Translator())
    assert song.units == ["El gato come.", "Sin embargo, María duerme.", "El gato sale del jardín."]
    song.study([w.lexeme.lexeme_id for w in song.new_words])
    assert conn.execute("SELECT skill FROM sessions").fetchone()[0] == "lyrics"
    sources = {
        r[0]
        for r in conn.execute("SELECT DISTINCT source FROM word_events WHERE turn_id IS NOT NULL")
    }
    assert sources == {"lyrics"}


def test_the_translation_is_grounded_in_the_expressions_reviewed_meaning(conn, lexicon):
    translate = Translator(lines(1, 2, 3))
    song = session(conn, translate)
    (expression,) = song.expressions()
    assert (expression.line_no, expression.phrase) == (2, "sin embargo")
    song.translation()
    prompt = translate.prompts[0]
    assert "1. El gato come.\n2. Sin embargo, María duerme.\n3. El gato sale del jardín." in prompt
    assert (
        "- line 2: «sin embargo» means: however, nevertheless (as in: «Sin embargo, no llueve.» "
        '= "However, it is not raining.")' in prompt
    )


def test_a_translation_is_stored_once_and_reused(conn, lexicon):
    first = session(conn, Translator(lines(1, 2, 3)))
    assert [t.natural_en for t in first.translation()] == ["natural 1", "natural 2", "natural 3"]
    run = conn.execute(
        "SELECT content_id, input_tokens, output_tokens FROM song_translations"
    ).fetchone()
    assert tuple(run) == (first.content_id, 900, 400)
    stored = conn.execute(
        "SELECT line_no, literal_en, note_en FROM song_translation_lines ORDER BY line_no"
    ).fetchall()
    assert [tuple(r) for r in stored] == [
        (1, "literal 1", None),
        (2, "literal 2", "figurative"),
        (3, "literal 3", None),
    ]

    again = LyricsSession(
        conn, Commenter(), LexiconIndex(conn), analyze_content, first.content_id, "recommended",
        translate=Translator(),  # no answers: a call would fail
    )  # fmt: skip
    assert [t.literal_en for t in again.translation()] == ["literal 1", "literal 2", "literal 3"]


def test_a_translation_missing_a_line_is_retried_once_then_refused(conn, lexicon):
    translate = Translator(lines(1, 3), lines(1, 2, 3))
    assert len(session(conn, translate).translation()) == 3
    assert "missed or repeated line numbers" in translate.prompts[1]
    assert conn.execute("SELECT output_tokens FROM song_translations").fetchone()[0] == 800

    broken = session(conn, Translator(lines(1, 1, 2), lines(2, 3)))
    with pytest.raises(TranslationError, match="3 lines exactly once"):
        broken.translation()
    assert conn.execute("SELECT COUNT(*) FROM song_translations").fetchone()[0] == 1


def test_attempts_are_compared_and_logged_without_crediting_anything(conn, lexicon):
    commenter = Commenter(
        AttemptFeedback(
            lines=[
                LineFeedback(line_no=2, verdict="close", comment_en="Good, but it means however."),
                LineFeedback(line_no=3, verdict="right", comment_en="Nobody asked."),  # not tried
            ]
        )
    )
    song = session(conn, Translator(lines(1, 2, 3)), commenter)
    compared = song.attempt({2: " Without embargo, María sleeps. ", 1: "   "})

    assert [(c.line_no, c.attempt, c.verdict) for c in compared] == [
        (1, None, None),
        (2, "Without embargo, María sleeps.", "close"),
        (3, None, None),
    ]
    assert compared[1].natural_en == "natural 2" and compared[1].note_en == "figurative"
    assert "Learner: Without embargo, María sleeps." in commenter.requests[0]
    turns = conn.execute(
        "SELECT role, kind, text_es, note_en, output_tokens FROM turns ORDER BY turn_no"
    ).fetchall()
    assert [tuple(t) for t in turns] == [
        (
            "learner",
            "attempt",
            "Sin embargo, María duerme.",
            "2: Without embargo, María sleeps.",
            None,
        ),
        (
            "tutor",
            "attempt",
            "Sin embargo, María duerme.",
            "2 (close): Good, but it means however.",
            120,
        ),
    ]
    assert (
        conn.execute("SELECT COUNT(*) FROM word_events WHERE event_type = 'used'").fetchone()[0]
        == 0
    )


def test_no_attempt_means_no_comparison_call(conn, lexicon):
    song = session(conn, Translator(lines(1, 2, 3)), Commenter())  # no scripted replies
    compared = song.attempt({})
    assert all(c.verdict is None for c in compared)
    assert conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0


def test_a_stored_translation_with_other_lines_is_not_used(conn, lexicon):
    song = session(conn, Translator(lines(1, 2, 3)))
    song.translation()
    assert stored_translation(conn, song.content_id, 3) is not None
    assert stored_translation(conn, song.content_id, 4) is None
    assert stored_translation(conn, 999, 3) is None
