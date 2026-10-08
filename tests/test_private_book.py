"""Splitting a PDF book you own into chapters: pages joined, paragraphs kept, titles found."""

import pytest

from spanish_tutor.ingest.private_book import join_pages, split_book

FULL = "x" * 70  # a full-width line


def test_a_paragraph_running_onto_the_next_page_is_one_paragraph():
    pages = [
        f"Primera línea {FULL}\nque sigue sin terminar {FULL}",
        f"y termina aquí.\n \nOtro párrafo {FULL}",
    ]
    text = join_pages(pages)
    assert text.split("\n\n")[0].endswith("y termina aquí.")
    assert len(text.split("\n\n")) == 2


def test_a_page_ending_a_paragraph_starts_a_new_one_on_the_next_page():
    pages = [f"Un párrafo {FULL}\nque termina.", f"Otro empieza aquí {FULL}"]
    assert join_pages(pages).split("\n\n") == [
        f"Un párrafo {FULL}\nque termina.",
        f"Otro empieza aquí {FULL}",
    ]


def test_a_word_hyphenated_across_lines_is_joined_but_a_dialogue_dash_is_not():
    text = join_pages([f"Era una pala-\nbra larga {FULL}\n-Hola -dijo-\nY se fue."])
    assert "palabra larga" in text
    assert "-dijo-\nY se fue" in text


def test_chapters_get_their_title_without_the_translators_credit():
    text = join_pages(
        [
            "Sinopsis: front matter that isn't part of the book.\n \nINDICE",
            (
                "CAPÍTULO 1\n \nTraducido por Alguien\n \nMI PRIMER DÍA EN NUEVA YORK\n \n"
                "Llegué a Nueva York un lunes."
            ),
            "CAPITULO 2\n \nTranscrito por Otra\n \nEL FINAL\n \nY así terminó.",
        ]
    )
    chapters = split_book(text + "\n \nVivía en Nueva York con mi madre.")
    assert [title for title, _ in chapters] == ["Mi primer día en Nueva York", "El final"]
    assert chapters[0][1] == "Llegué a Nueva York un lunes."
    assert not any("Traducido" in body or "Transcrito" in body for _, body in chapters)


def test_a_chapter_without_a_title_in_capitals_is_numbered():
    assert split_book("CAPÍTULO 7\n\nEl texto empieza aquí.") == [
        ("Capítulo 7", "El texto empieza aquí.")
    ]


def test_a_book_without_chapter_headings_fails_loudly():
    with pytest.raises(ValueError, match="no chapter heading"):
        split_book("Solo texto, sin capítulos.")
