"""Fixtures shared by the word-bank, teaching and conversation tests."""

import json

import pytest

from spanish_tutor.db import connect, init_schema
from spanish_tutor.ingest.wiktionary import Wiktionary


@pytest.fixture
def conn():
    conn = connect(":memory:")
    init_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def make_wiktionary(tmp_path):
    """Build a Wiktionary from (word, wiktionary_pos, gloss) triples."""

    def make(entries: list[tuple[str, str, str]]) -> Wiktionary:
        path = tmp_path / "wiktionary.jsonl"
        path.write_text(
            "".join(
                json.dumps({"word": w, "pos": p, "senses": [{"gloss": g, "tags": []}]}) + "\n"
                for w, p, g in entries
            ),
            encoding="utf-8",
        )
        return Wiktionary(path)

    return make
