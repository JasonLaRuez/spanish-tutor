"""The real lexicon reviewer against the API: one request, two planted errors (~1 cent).

    uv run pytest -m live

Checks what the fast tests can't: the model accepts the request (effort, structured output),
the answer parses, it flags a wrong definition and a wrong example translation, and it
leaves correct entries alone.
"""

import pytest

from spanish_tutor.evaluation import lexicon_review as lr

pytestmark = pytest.mark.live


def test_the_reviewer_flags_planted_errors_and_passes_correct_entries():
    batch = [
        lr.Entry(1, "casa", "NOUN", "house, home", "wiktionary", "Mi casa es tu casa.", "My house is your house."),
        lr.Entry(2, "perro", "NOUN", "cat", "wiktionary", "El perro ladra.", "The dog barks."),  # wrong definition
        lr.Entry(3, "comer", "VERB", "to eat", "wiktionary", "Quiero comer pan.", "I want to drink water."),  # wrong translation
        lr.Entry(4, "rápido", "ADJ", "fast, quick", "wiktionary", "Es un coche rápido.", "It's a fast car."),
    ]  # fmt: skip
    problems, tokens_in, tokens_out = lr.claude_ask(lr.REVIEWER)(lr.prompt(batch))
    rows = lr.verdict_rows(batch, problems)
    flagged = {(r[0], r[1]) for r in rows if r[2] != "correct"}
    assert (2, "definition_en") in flagged
    assert (3, "example_en") in flagged
    assert not any(lexeme_id in (1, 4) for lexeme_id, _ in flagged)
    print(f"\n{tokens_in} in / {tokens_out} out tokens; {problems}")
