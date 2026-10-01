"""The real embedding model. Slow (loads ~300 MB): run with `uv run pytest -m slow`."""

import numpy as np
import pytest

from spanish_tutor.embeddings import DIMENSIONS, load_embeddings

pytestmark = pytest.mark.slow


def test_model_returns_unit_vectors_of_expected_size():
    vector = np.array(load_embeddings().embed_query("Hola, ¿qué tal?"))
    assert vector.shape == (DIMENSIONS,)
    assert np.linalg.norm(vector) == pytest.approx(1.0, abs=1e-3)


def test_translations_are_closer_than_unrelated_sentences():
    hot_es, hot_en, read_es, read_en = np.array(
        load_embeddings().embed_documents(
            [
                "Hace mucho calor hoy",
                "It is very hot today",
                "Me gusta leer libros",
                "I like reading books",
            ]
        )
    )
    assert hot_es @ hot_en > hot_es @ read_es + 0.5
    assert read_es @ read_en > read_es @ hot_en + 0.5
