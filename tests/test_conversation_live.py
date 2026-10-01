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
