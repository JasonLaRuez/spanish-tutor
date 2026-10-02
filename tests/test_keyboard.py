"""Typed accent markers become Spanish characters."""

import pytest

from spanish_tutor.keyboard import expand_markers


@pytest.mark.parametrize(
    "typed, spanish",
    [
        ("'a 'e 'i 'o 'u", "á é í ó ú"),
        ("'A 'E 'I 'O 'U", "Á É Í Ó Ú"),
        ("ma~nana, ~Nu~noa", "mañana, Ñuñoa"),
        ("ping:uino, verg:uenza, :U", "pingüino, vergüenza, Ü"),
        ("detr'as de la casa", "detrás de la casa"),
    ],
)
def test_markers_become_spanish_characters(typed, spanish):
    assert expand_markers(typed) == spanish


@pytest.mark.parametrize(
    "typed, spanish",
    [
        ("?Como est'as?", "¿Como estás?"),
        ("!Hola! ?Qu'e tal?", "¡Hola! ¿Qué tal?"),
        ("Bien, ?y t'u?", "Bien, ¿y tú?"),
        ('Dice "?por qu\'e?"', 'Dice "¿por qué?"'),
        ("?'Arbol?", "¿Árbol?"),  # the word starts with a marker
    ],
)
def test_a_mark_directly_before_a_word_opens(typed, spanish):
    assert expand_markers(typed) == spanish


@pytest.mark.parametrize(
    "text",
    [
        "Hola! Que tal?",  # closing marks stay
        "Hola!Que tal",  # not at a word start
        "? Hola",  # a space after the mark: not directly before a word
        "I'm sure it's fine",  # apostrophes not before a vowel
        "Tengo 3 libros.",
    ],
)
def test_text_without_markers_is_unchanged(text):
    assert expand_markers(text) == text


def test_expanding_twice_changes_nothing_more():
    once = expand_markers("?Qu'e tal? Ma~nana voy al ping:uino!")
    assert once == "¿Qué tal? Mañana voy al pingüino!"
    assert expand_markers(once) == once
