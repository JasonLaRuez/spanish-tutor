"""The real English check against the API: one call on made-up Spanglish lines (~1-2 cents).

    uv run pytest -m live

Checks what the fast tests can't: the model accepts the request and the answer parses, and it
judges words in context: "come" and "me" are English in "so come with me" but Spanish in "el
gato come" and "me gusta"; "party" and "baby" inside Spanish phrases are loanwords.
"""

import pytest

from spanish_tutor import english
from spanish_tutor.conversation import ClaudeGenerator

pytestmark = pytest.mark.live

LINES = [
    "Baby, yo te quiero, you know I need you",
    "Me gusta cuando you dance con la luna",
    "So come with me, ven conmigo",
    "El gato come y la party no para",
    "Tonight vamos a bailar, mi baby",
]


def test_english_and_loanwords_are_judged_in_context():
    marks, generation = english.mark("Canción de prueba", LINES, ClaudeGenerator(effort="low").ask)
    english_words = {
        n: {w for w, k in words.items() if k == "english"} for n, words in marks.items()
    }
    loanwords = {n: {w for w, k in words.items() if k == "loanword"} for n, words in marks.items()}

    assert {"you", "know", "need"} <= english_words[1]
    assert {"you", "dance"} <= english_words[2] and "me" not in english_words[2]
    assert {"so", "come", "with", "me"} <= english_words[3]
    assert "come" not in english_words.get(4, set())  # el gato come: Spanish
    assert "party" in loanwords.get(4, set())
    assert "tonight" in english_words[5]
    assert "baby" in loanwords.get(5, set()) | english_words[5]
    tokens_in = generation.input_tokens + generation.cache_read_tokens
    cost = (tokens_in * english.PRICE_IN + generation.output_tokens * english.PRICE_OUT) / 1e6
    print(
        f"\n{tokens_in} in / {generation.output_tokens} out tokens, ${cost:.4f}; estimate ${english.estimate_cost(len(LINES)):.4f}"
    )
    print(marks)
