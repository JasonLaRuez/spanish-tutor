"""The real resolver against the API: one call, four forms (well under a cent).

    uv run pytest -m live

Checks what the fast tests can't: the model accepts the request (effort, structured
output), the answer parses, and it gets clear cases right: an old spelling and a lyric
elision are variants of existing words, English is not Spanish, and a real word that
Wiktionary lacks ("parrandeo", Caribbean for partying) gets a definition.
"""

import pytest

from spanish_tutor.resolve import ClaudeResolver, Pending

pytestmark = pytest.mark.live


def test_clear_cases_get_the_right_verdicts():
    pending = [
        Pending("fué", "fuar", "VERB", "Aquello fué lo mejor del viaje.", 3),
        Pending("pa'", "pa'", "ADP", "Vamos pa' la playa, mi amor", 2),
        Pending("yeah", "yeah", "NOUN", "Yeah, yeah, bailando toda la noche", 2),
        Pending(
            "parrandeo", "parrandeo", "NOUN", "Después de tanto parrandeo nadie se levantó.", 1
        ),
    ]
    resolver = ClaudeResolver()
    asked = resolver("song", pending)
    by_id = {r.id: r for r in asked.resolutions}

    assert sorted(by_id) == [1, 2, 3, 4]
    assert (by_id[1].verdict, by_id[1].lemma, by_id[1].pos) == ("variant", "ser", "VERB")
    assert (by_id[2].verdict, by_id[2].lemma, by_id[2].pos) == ("variant", "para", "ADP")
    assert by_id[3].verdict == "not_spanish"
    assert by_id[4].verdict in ("word", "variant") and by_id[4].lemma
    if by_id[4].verdict == "word":
        assert by_id[4].definition_en and by_id[4].example_en
    assert asked.input_tokens > 0 and asked.output_tokens > 0
    print(f"\n{asked.input_tokens} in / {asked.output_tokens} out tokens")
    for r in asked.resolutions:
        print(r.model_dump())
