"""The reading skill (reading.py), with the content fakes and a scripted model: no spaCy,
no API calls."""

import pytest
from fakes import Scripted, add_content_lexicon, analyze_content, story
from langchain_core.embeddings import DeterministicFakeEmbedding

from spanish_tutor.reading import ReadingSession, TextPassages
from spanish_tutor.words import LexiconIndex

TEXT = "El gato come. Sin embargo, María duerme.\n\nEl gato sale del jardín. Llueve."


@pytest.fixture
def lexicon(conn):
    ids = add_content_lexicon(conn)
    # The learner knows el and gato (and de): everything else in TEXT is new.
    conn.executemany(
        "INSERT INTO word_events (lexeme_id, mode, event_type, source) "
        "VALUES (?, 'recognition', 'taught', 'seed')",
        [(ids["el"],), (ids["gato"],), (ids["de"],)],
    )
    return ids


def reading(conn, text=TEXT, generate=None):
    item = story(conn, text)
    conn.commit()
    return ReadingSession(
        conn, generate or Scripted(), LexiconIndex(conn), analyze_content, item, "recommended"
    )


def turns(conn, session_id):
    return [
        tuple(r)
        for r in conn.execute(
            "SELECT turn_no, role, kind, text_es FROM turns WHERE session_id = ? ORDER BY turn_no",
            (session_id,),
        )
    ]


def events(conn, session_id):
    return sorted(
        tuple(r)
        for r in conn.execute(
            """
            SELECT l.lemma, e.event_type, e.source FROM word_events AS e
            JOIN turns AS t USING (turn_id) JOIN lexemes AS l USING (lexeme_id)
            WHERE t.session_id = ?
            """,
            (session_id,),
        )
    )


def lemmas(words):
    return [w.lexeme.lemma for w in words]


def test_new_words_come_in_order_of_first_appearance(conn, lexicon):
    session = reading(conn)
    # Known words (el, gato, de) and the name (María) are left out; "sin embargo" is one word.
    assert lemmas(session.new_words) == [
        "comer",
        "sin embargo",
        "dormir",
        "salir",
        "jardín",
        "llover",
    ]
    assert [w.sentence for w in session.new_words] == [0, 1, 1, 2, 2, 3]
    assert session.new_words[1].context == "Sin embargo, María duerme."


def test_starting_a_session_logs_the_start_with_its_session(conn, lexicon):
    session = reading(conn)
    row = conn.execute("SELECT event, chosen_via, session_id FROM content_events").fetchone()
    assert tuple(row) == ("started", "recommended", session.session_id)
    assert conn.execute("SELECT skill, topic FROM sessions").fetchone()[:] == ("reading", "Cuento")


def test_a_batch_is_studied_as_one_turn_and_never_offered_again(conn, lexicon):
    session = reading(conn)
    batch = session.next_batch(2)
    assert [w.lexeme.lemma for w, _ in batch] == ["comer", "sin embargo"]
    assert [lesson.lemma for _, lesson in batch] == ["comer", "sin embargo"]
    assert turns(conn, session.session_id) == []  # offering a batch logs nothing

    studied = session.study([w.lexeme.lexeme_id for w, _ in batch] + [lexicon["el"]])  # el: known

    assert [lex.lemma for lex in studied] == ["comer", "sin embargo"]
    assert turns(conn, session.session_id) == [(1, "tutor", "study", "comer, sin embargo")]
    assert events(conn, session.session_id) == [
        ("comer", "taught", "reading"),
        ("sin embargo", "taught", "reading"),
    ]
    assert lemmas(session.remaining)[:1] == ["dormir"]
    assert [w.lexeme.lemma for w, _ in session.next_batch(2)] == ["dormir", "salir"]


def test_how_far_every_word_is_studied_and_which_words_are_still_marked(conn, lexicon):
    session = reading(conn)
    assert session.readable_until() == 0
    assert {"come", "sin", "embargo", "duerme", "llueve"} <= session.unstudied_forms()
    session.study([w.lexeme.lexeme_id for w in session.new_words[:3]])  # comer .. dormir
    assert session.readable_until() == 2  # sentences 0 and 1 are fully studied
    assert "come" not in session.unstudied_forms() and "sale" in session.unstudied_forms()


def test_looking_up_teaches_an_unknown_word_and_reminds_of_a_known_one(conn, lexicon):
    session = reading(conn)
    found = session.look_up("llueve")
    assert found.lemma == "llover"
    assert turns(conn, session.session_id) == [(1, "tutor", "study", "llover")]
    assert session.look_up("gato").lemma == "gato"  # known: free, nothing logged
    assert len(turns(conn, session.session_id)) == 1
    assert session.look_up("xyzzy") is None
    assert "llover" not in lemmas(session.remaining)


def test_finishing_logs_seen_for_the_known_words_and_finishes_the_item(conn, lexicon):
    session = reading(conn)
    session.study([lexicon["comer"]])
    session.finish()
    session.finish()  # twice: nothing more

    assert turns(conn, session.session_id)[-1] == (2, "learner", "reading", "Cuento")
    seen = [lemma for lemma, kind, _ in events(conn, session.session_id) if kind == "seen"]
    assert seen == ["comer", "de", "el", "gato"]  # known or studied; never the unstudied ones
    finished = conn.execute(
        "SELECT session_id FROM content_events WHERE event = 'finished'"
    ).fetchall()
    assert [r[0] for r in finished] == [session.session_id]
    assert conn.execute("SELECT ended_at FROM sessions").fetchone()[0] is not None


def test_a_form_resolved_earlier_counts_as_that_word(conn, lexicon):
    conn.execute(
        "INSERT INTO word_resolutions (form, tagged_lemma, tagged_pos, verdict, lexeme_id, "
        "reviewer) VALUES ('fué', 'fuar', 'VERB', 'variant', ?, 'human')",
        (lexicon["ser"],),
    )
    session = reading(conn, "El gato fué.")
    assert lemmas(session.new_words) == ["ser"]


def test_the_discussion_continues_the_session_with_passages_from_the_text(conn, lexicon):
    generate = Scripted("¿Te gustó el cuento?", "¡Sí! El gato come.")
    session = reading(conn, generate=generate)
    session.study([w.lexeme.lexeme_id for w in session.new_words])
    session.finish()

    tutor = session.discuss(DeterministicFakeEmbedding(size=32))
    tutor.open()
    tutor.respond("El gato come.")

    assert tutor.session_id == session.session_id
    numbers = [n for n, *_ in turns(conn, session.session_id)]
    assert numbers == list(range(1, len(numbers) + 1))  # one numbering for the whole session
    opening = generate.requests[0][-2].content[0]["text"]
    assert "just finished reading «Cuento»" in opening
    note = generate.requests[1][-1].content
    assert "Passages from the text the learner read" in note
    assert "El gato come." in note
    sources = {source for _, _, source in events(conn, session.session_id)}
    assert sources == {"reading"}


def test_passages_group_sentences_and_return_the_closest(conn):
    passages = TextPassages(
        ["Uno.", "Dos.", "Tres.", "Cuatro."], DeterministicFakeEmbedding(size=32), size=3
    )
    assert passages.passages == ["Uno. Dos. Tres.", "Cuatro."]
    assert len(passages.search("Cuatro.", k=1)) == 1
    assert TextPassages([], DeterministicFakeEmbedding(size=32)).search("x") == []
