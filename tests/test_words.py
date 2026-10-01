"""Resolving analyses to lexemes (accent fallback, Wiktionary, non-words) and logging events."""

import pytest

from spanish_tutor.words import (
    GRADE_MISUSED,
    Event,
    LexiconIndex,
    ensure_lexeme,
    fold_accents,
    log_events,
)


def add_lexeme(conn, lemma, pos="NOUN"):
    return conn.execute("INSERT INTO lexemes (lemma, pos) VALUES (?, ?)", (lemma, pos)).lastrowid


def lexeme_count(conn):
    return conn.execute("SELECT COUNT(*) FROM lexemes").fetchone()[0]


@pytest.mark.parametrize(
    "text, folded",
    [("jardín", "jardin"), ("difícil", "dificil"), ("pingüino", "pinguino"), ("año", "año")],
)
def test_fold_accents_strips_accents_but_keeps_ñ(text, folded):
    assert fold_accents(text) == folded


# --- LexiconIndex.resolve ---------------------------------------------------------------


def test_exact_match_wins(conn):
    casa = add_lexeme(conn, "casa")
    lexeme = LexiconIndex(conn).resolve(("casa", "NOUN"))
    assert (lexeme.lexeme_id, lexeme.lemma) == (casa, "casa")


def test_missing_accent_resolves_to_the_only_accented_match(conn):
    jardin = add_lexeme(conn, "jardín")
    lexeme = LexiconIndex(conn).resolve(("jardin", "NOUN"))
    assert (lexeme.lexeme_id, lexeme.lemma) == (jardin, "jardín")


def test_accent_fallback_respects_part_of_speech(conn):
    add_lexeme(conn, "difícil", "ADJ")
    assert LexiconIndex(conn).resolve(("dificil", "NOUN")) is None


def test_ambiguous_accent_fallback_is_not_guessed(conn):
    # Two words with the same POS fold to "solo" (the second is invented): neither is chosen.
    add_lexeme(conn, "sólo", "ADV")
    add_lexeme(conn, "soló", "ADV")
    assert LexiconIndex(conn).resolve(("solo", "ADV")) is None


def test_a_spelling_that_is_its_own_word_is_never_re_accented(conn):
    esta_det = add_lexeme(conn, "esta", "DET")
    add_lexeme(conn, "está", "VERB")
    lexeme = LexiconIndex(conn).resolve(("esta", "DET"))
    assert (lexeme.lexeme_id, lexeme.lemma) == (esta_det, "esta")


def test_wiktionary_word_missing_from_the_lexicon_is_added(conn, make_wiktionary):
    wiktionary = make_wiktionary([("nadar", "verb", "to swim")])
    lexeme = LexiconIndex(conn, wiktionary).resolve(("nadar", "VERB"))
    row = conn.execute("SELECT * FROM lexemes WHERE lexeme_id = ?", (lexeme.lexeme_id,)).fetchone()
    assert (row["lemma"], row["definition_en"], row["definition_source"]) == (
        "nadar",
        "to swim",
        "wiktionary",
    )
    assert row["frequency_per_million"] is None


def test_non_word_resolves_to_none_and_writes_nothing(conn, make_wiktionary):
    index = LexiconIndex(conn, make_wiktionary([("saber", "verb", "to know")]))
    assert index.resolve(("sabo", "NOUN")) is None
    assert lexeme_count(conn) == 0


def test_lookup_never_writes_or_guesses(conn, make_wiktionary):
    add_lexeme(conn, "jardín")
    index = LexiconIndex(conn, make_wiktionary([("nadar", "verb", "to swim")]))
    assert index.lookup(("jardin", "NOUN")) is None
    assert index.lookup(("nadar", "VERB")) is None
    assert lexeme_count(conn) == 1


def test_ensure_lexeme_returns_existing_id_without_inserting(conn, make_wiktionary):
    casa = add_lexeme(conn, "casa")
    assert ensure_lexeme(conn, make_wiktionary([("casa", "noun", "house")]), "casa", "NOUN") == casa
    assert lexeme_count(conn) == 1


def test_ensure_lexeme_without_wiktionary_only_finds_existing_words(conn):
    assert ensure_lexeme(conn, None, "nadar", "VERB") is None


def test_resolved_new_words_are_cached_in_the_index(conn, make_wiktionary):
    index = LexiconIndex(conn, make_wiktionary([("nadar", "verb", "to swim")]))
    first = index.resolve(("nadar", "VERB"))
    assert index.resolve(("nadar", "VERB")) == first
    assert lexeme_count(conn) == 1


# --- Events ----------------------------------------------------------------------------


def test_events_derive_their_mode_from_the_event_type():
    assert Event(1, "used", "conversation").mode == "production"
    assert {Event(1, t, "conversation").mode for t in ("taught", "seen", "looked_up")} == {
        "recognition"
    }


def test_logged_events_update_the_word_bank(conn):
    casa = add_lexeme(conn, "casa")
    log_events(
        conn,
        [
            Event(casa, "taught", "conversation"),
            Event(casa, "used", "conversation", grade=GRADE_MISUSED),
        ],
    )
    rows = {
        r["mode"]: r for r in conn.execute("SELECT * FROM word_bank WHERE lexeme_id = ?", (casa,))
    }
    assert set(rows) == {"recognition", "production"}
    assert rows["production"]["familiarity"] == pytest.approx(GRADE_MISUSED / 5)
    assert rows["recognition"]["familiarity"] is None


def test_headword_prefers_the_exact_spelling_then_the_most_frequent_pos(conn):
    conn.execute(
        "INSERT INTO lexemes (lemma, pos, frequency_per_million) VALUES ('bajo', 'ADJ', 50)"
    )
    conn.execute(
        "INSERT INTO lexemes (lemma, pos, frequency_per_million) VALUES ('bajo', 'ADP', 90)"
    )
    conn.execute("INSERT INTO lexemes (lemma, pos) VALUES ('más', 'ADV')")
    conn.execute("INSERT INTO lexemes (lemma, pos) VALUES ('mas', 'CCONJ')")
    index = LexiconIndex(conn)
    assert index.headword("bajo").pos == "ADP"
    assert index.headword("mas").lemma == "mas"  # exact spelling beats the accented word
    assert index.headword("perro") is None


def test_headword_without_accents_needs_a_single_candidate(conn):
    add_lexeme(conn, "jardín")
    add_lexeme(conn, "sólo", "ADV")
    add_lexeme(conn, "soló", "VERB")
    index = LexiconIndex(conn)
    assert index.headword("jardin").lemma == "jardín"
    assert index.headword("solo") is None
