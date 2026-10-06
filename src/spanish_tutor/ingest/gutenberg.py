"""Public-domain books from Project Gutenberg, split into chapter files for content.py.

    uv run python -m spanish_tutor.ingest.gutenberg 13507
    uv run python -m spanish_tutor.content add-book data/raw/gutenberg/13507 --title ... \\
        --author ... --source gutenberg:13507

Downloads the plain-text edition into data/raw/gutenberg/ (gitignored, like all data),
strips Project Gutenberg's header and license, and writes one numbered file per chapter.

Chapter headings differ from book to book. This splitter reads the markup of Gutenberg's
Spanish texts converted from the same source as the first book used (13507, Quiroga's
"Cuentos de amor de locura y de muerte", 1917): headings wrapped in #...#, with a chapter
heading in capitals (#LA GALLINA DEGOLLADA#) and section headings mixed-case
(#Primavera#). Chapter titles come from the book's table of contents (#INDICE#), in
order, since the headings drop accents. A book with other markup needs another splitter
(roadmap Phase 4).
"""

import argparse
import re
import sys
from pathlib import Path

from spanish_tutor.ingest.download import RAW_DIR, fetch

GUTENBERG_DIR = RAW_DIR / "gutenberg"
URL = "https://www.gutenberg.org/cache/epub/{n}/pg{n}.txt"

# [ \t]*, not \s*: \s would also swallow the blank lines after a heading, fusing a section
# heading into its paragraph's first sentence ("Primavera Era el martes...").
_HEADING = re.compile(r"^#([^#\n]+)#[ \t]*$", re.MULTILINE)
_CHAPTER_HEADING = re.compile(r"^#([^a-z#\n]+)#[ \t]*$", re.MULTILINE)  # no lowercase: capitals


def body(text: str) -> str:
    """The book without Project Gutenberg's header and license."""
    start = text.index("*** START OF")
    start = text.index("\n", start) + 1
    return text[start : text.index("*** END OF")]


def table_of_contents(text: str) -> list[str]:
    """The titles listed under #INDICE#, up to the next heading."""
    after = text.split("#INDICE#", 1)[1]
    return [line.strip() for line in after.split("#", 1)[0].splitlines() if line.strip()]


def split_chapters(text: str) -> list[tuple[str, str]]:
    """(title, text) per chapter, in order.

    Each capitalized heading after the table of contents starts a chapter. Section
    headings inside a chapter become plain lines, and the double hyphen Gutenberg uses for
    the dialogue dash becomes a spaced em dash, so the tagger doesn't fuse it to words.
    """
    titles = table_of_contents(text)
    after_toc = text.split("#INDICE#", 1)[1]
    parts = _CHAPTER_HEADING.split(after_toc)
    chapters = parts[2::2]  # parts: [toc, heading, text, heading, text, ...]
    if len(chapters) != len(titles):
        raise ValueError(f"{len(chapters)} chapter headings, but {len(titles)} titles listed")
    return [
        (title, _HEADING.sub(r"\1", chapter).replace("--", " — ").strip())
        for title, chapter in zip(titles, chapters, strict=True)
    ]


def write_chapters(chapters: list[tuple[str, str]], folder: Path) -> list[Path]:
    """One file per chapter, named so that sorting by name gives reading order."""
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for number, (title, text) in enumerate(chapters, 1):
        path = folder / f"{number:02} {title}.txt"
        path.write_text(text + "\n", encoding="utf-8")
        paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Download a Gutenberg book, split by chapter.")
    parser.add_argument("ebook", type=int, help="Project Gutenberg ebook number, e.g. 13507")
    args = parser.parse_args()
    GUTENBERG_DIR.mkdir(parents=True, exist_ok=True)
    source = GUTENBERG_DIR / f"pg{args.ebook}.txt"
    fetch(URL.format(n=args.ebook), source)
    try:
        chapters = split_chapters(body(source.read_text(encoding="utf-8")))
    except (ValueError, IndexError) as error:
        sys.exit(f"can't split this book with the #HEADING# splitter: {error}")
    paths = write_chapters(chapters, GUTENBERG_DIR / str(args.ebook))
    for path, (_, text) in zip(paths, chapters, strict=True):
        print(f"{path.name}: {len(text.split()):,} words")


if __name__ == "__main__":
    main()
