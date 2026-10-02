"""The real Claude integration: two turns against the API (about 1 cent).

    uv run pytest -m live

Checks what the fast tests can't: the model accepts the request settings (effort,
structured output, refusal fallback, mid-conversation system notes), the reply parses,
and the stable prefix is served from the prompt cache on the second turn. The word bank
is empty and every word analyzes to nothing in the lexicon, so there are no retries:
exactly two API calls.
"""

import pytest

from spanish_tutor.conversation import ClaudeGenerator, Tutor
from spanish_tutor.topics import Candidate, choose_words
from spanish_tutor.words import LexiconIndex

pytestmark = pytest.mark.live


def analyze(text):
    return [(word, []) for word in text.split()]


def test_two_real_turns_parse_and_hit_the_prompt_cache(conn):
    tutor = Tutor(conn, ClaudeGenerator(), LexiconIndex(conn), analyze, topic="el tiempo")
    opening = tutor.open()
    reply = tutor.respond("Hoy hace sol y calor.")

    assert opening.reply_es and opening.reply_en
    assert reply.reply_es and reply.reply_en
    rows = conn.execute("SELECT * FROM turns ORDER BY turn_no").fetchall()
    assert [r["role"] for r in rows] == ["tutor", "learner", "tutor"]
    assert rows[2]["cache_read_tokens"] > 0  # instructions + vocabulary came from the cache
    assert rows[2]["output_tokens"] > 0


def test_topic_selection_and_translation_parse(conn):
    generator = ClaudeGenerator()
    pool = [
        Candidate("regar", "VERB", "to water (plants)", 10, 5.0),
        Candidate("césped", "NOUN", "lawn", 5, 3.0),
        Candidate("planta", "NOUN", "plant", 14, 4.0),
        Candidate("lleno", "ADJ", "full", 9, 1.0),
    ]
    chosen = choose_words(generator.select_words, "el jardín", pool, 2)
    assert len(chosen) == 2 and all(c in pool for c in chosen)

    tutor = Tutor(conn, generator, LexiconIndex(conn), analyze, topic="el jardín")
    tutor.open()
    turn = tutor.translate('Como se dice "the dog"?', "the dog")
    assert "perro" in turn.spanish.lower()
    assert turn.explanation_en
    rows = conn.execute("SELECT kind, cache_read_tokens FROM turns ORDER BY turn_no").fetchall()
    assert [r["kind"] for r in rows] == ["conversation", "translation", "translation"]
    # One output schema for every conversation request: the translation reads the cache
    # the opening wrote, instead of writing its own (~5k tokens at the 1-hour price).
    assert rows[2]["cache_read_tokens"] > 0
