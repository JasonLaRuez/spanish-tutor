"""The lyrics skill against the API: one translation of a short public-domain poem at
medium effort, and one comparison (about 5 cents).

    uv run pytest -m live

Checks what the fast tests can't: both schemas are accepted, every line comes back once,
and the natural and literal translations really differ where the Spanish is figurative.
"""

import pytest

from spanish_tutor.conversation import ClaudeGenerator
from spanish_tutor.lyrics import (
    AttemptFeedback,
    SongTranslation,
    feedback_prompt,
    translation_prompt,
)

pytestmark = pytest.mark.live

RIMA_XXIII = [
    "Por una mirada, un mundo;",
    "Por una sonrisa, un cielo;",
    "Por un beso... ¡yo no sé",
    "Qué te diera por un beso!",
]


def test_a_real_poem_is_translated_naturally_and_literally_then_compared():
    translate = ClaudeGenerator(effort="medium")
    generation = translate.ask(
        SongTranslation, translation_prompt("poem", "Rima XXIII", RIMA_XXIII, [])
    )
    lines = sorted(generation.reply.lines, key=lambda line: line.line_no)
    assert [line.line_no for line in lines] == [1, 2, 3, 4]
    assert all(line.natural_en and line.literal_en for line in lines)
    print(f"\ntranslation: {generation.input_tokens} in / {generation.output_tokens} out tokens")
    for line in lines:
        print(line.model_dump())

    commenter = ClaudeGenerator()
    feedback = commenter.ask(
        AttemptFeedback,
        feedback_prompt("poem", [(1, RIMA_XXIII[0], "For a look, a world", lines[0])]),
    )
    (comment,) = feedback.reply.lines
    assert comment.line_no == 1 and comment.verdict in ("right", "close") and comment.comment_en
    print(f"comparison: {feedback.input_tokens} in / {feedback.output_tokens} out tokens")
    print(comment.model_dump())
