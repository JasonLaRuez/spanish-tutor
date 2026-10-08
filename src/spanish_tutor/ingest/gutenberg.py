"""Public-domain books from Project Gutenberg, split into chapter files for content.py.

    uv run python -m spanish_tutor.ingest.gutenberg 13507
    uv run python -m spanish_tutor.content add-book data/raw/gutenberg/13507 --title ... \\
        --author ... --source gutenberg:13507

Downloads the plain-text edition into data/raw/gutenberg/ (gitignored, like all data),
strips Project Gutenberg's header and license, and writes one numbered file per chapter.

Every book is laid out differently (headings in #...#, in capitals with numbers in the
margin, notes in English, illustrations), so each book has a small manifest in
ingest/books/<ebook>.json, written by hand after reading the text. The manifests are
committed (the text never is):

    kind      "book" (the default: chapters of one book, read in order), "poems" or
              "stories" (a collection: each chapter is a poem or story of its own, ranked
              on its own, added with add-poems or add-stories; the title is the collection)
    chapters  [heading as it appears in the text, title to show], in reading order. Each
              heading must be found, in order, as a whole line; the splitter fails loudly if
              one isn't, rather than guess. A collection of several writers' stories or
              poems may add a third element, that item's author (written to chapters.json
              beside the chapter files, with each exact title; the add commands read it).
    start     the line after which the headings are searched (a section heading such as
              RIMAS, when earlier sections number their parts the same way), or null
    end       the line where the reading text ends (a vocabulary or notes section), or null
    remove    regexes deleted from the text before splitting (margin line numbers, notes)
    replace   [regex, replacement] applied to each chapter's text (markup, dialogue dashes)
"""

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from spanish_tutor.ingest.download import RAW_DIR, fetch

GUTENBERG_DIR = RAW_DIR / "gutenberg"
MANIFESTS = Path(__file__).with_name("books")
URL = "https://www.gutenberg.org/cache/epub/{n}/pg{n}.txt"
CHAPTERS_FILE = "chapters.json"  # chapter file name -> its exact title, and author if given
UNSAFE_IN_NAMES = re.compile(r'[<>:"/\\|?*]')  # characters Windows forbids in file names


@dataclass(frozen=True)
class Manifest:
    ebook: int
    title: str
    author: str | None
    chapters: list[tuple[str, str]]  # (heading in the text, title to show)
    authors: tuple[str | None, ...] = ()  # per chapter, for a collection of several writers
    kind: str = "book"  # or "poems", "stories"
    start: str | None = None
    end: str | None = None
    remove: tuple[str, ...] = ()
    replace: tuple[tuple[str, str], ...] = ()


def load_manifest(ebook: int, folder: Path = MANIFESTS) -> Manifest:
    path = folder / f"{ebook}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"no manifest for ebook {ebook}: read the text and write {path} (see this module)"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("kind", "book") not in ("book", "poems", "stories"):
        raise ValueError(f"{path.name}: kind must be book, poems or stories")
    return Manifest(
        ebook=data["ebook"],
        title=data["title"],
        author=data.get("author"),
        chapters=[(chapter[0], chapter[1]) for chapter in data["chapters"]],
        authors=tuple(chapter[2] if len(chapter) > 2 else None for chapter in data["chapters"]),
        kind=data.get("kind", "book"),
        start=data.get("start"),
        end=data.get("end"),
        remove=tuple(data.get("remove", [])),
        replace=tuple((pattern, repl) for pattern, repl in data.get("replace", [])),
    )


def body(text: str) -> str:
    """The book without Project Gutenberg's header and license."""
    start = text.index("*** START OF")
    start = text.index("\n", start) + 1
    return text[start : text.index("*** END OF")]


def _line_at(text: str, line: str, start: int) -> re.Match[str] | None:
    """The first whole line equal to `line` (ignoring surrounding spaces) from `start`."""
    return re.compile(rf"^[ \t]*{re.escape(line)}[ \t]*$", re.MULTILINE).search(text, start)


def split_chapters(text: str, manifest: Manifest) -> list[tuple[str, str]]:
    """(title, text) per chapter, in order, as the manifest describes.

    The `remove` patterns run first, so a heading with a margin number still matches.
    Headings are matched as whole lines (a contents list numbering them, "I. EL CUENTO",
    doesn't match), each after the previous one. A chapter runs from the end of its heading
    line to the next heading, or to `end`, or to the end of the book.
    """
    for pattern in manifest.remove:
        text = re.sub(pattern, "", text, flags=re.MULTILINE)
    position = 0
    if manifest.start is not None:
        match = _line_at(text, manifest.start, 0)
        if match is None:
            raise ValueError(f"start line not found: {manifest.start!r}")
        position = match.end()
    found = []
    for heading, title in manifest.chapters:
        match = _line_at(text, heading, position)
        if match is None:
            raise ValueError(f"heading not found after position {position}: {heading!r}")
        found.append((title, match))
        position = match.end()
    stop = len(text)
    if manifest.end is not None:
        match = _line_at(text, manifest.end, position)
        if match is None:
            raise ValueError(f"end line not found after the last chapter: {manifest.end!r}")
        stop = match.start()
    chapters = []
    for i, (title, match) in enumerate(found):
        until = found[i + 1][1].start() if i + 1 < len(found) else stop
        chapter = text[match.end() : until]
        for pattern, repl in manifest.replace:
            chapter = re.sub(pattern, repl, chapter, flags=re.MULTILINE)
        chapters.append((title, chapter.strip()))
    return chapters


def write_chapters(
    chapters: list[tuple[str, str]],
    folder: Path,
    authors: Sequence[str | None] = (),
) -> list[Path]:
    """One file per chapter, named so that sorting by name gives reading order, and
    chapters.json beside them: each file's exact title (a file name can't hold ? or : on
    Windows, so the name is only a readable approximation) and, for a collection of several
    writers, its author. Files from an earlier split are replaced."""
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.txt"):
        old.unlink()
    paths, info = [], {}
    for number, (title, text) in enumerate(chapters, 1):
        path = folder / f"{number:02} {UNSAFE_IN_NAMES.sub('', title).strip(' .')}.txt"
        path.write_text(text + "\n", encoding="utf-8")
        paths.append(path)
        author = authors[number - 1] if number <= len(authors) else None
        info[path.name] = {"title": title} | ({"author": author} if author else {})
    (folder / CHAPTERS_FILE).write_text(
        json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Download a Gutenberg book, split by chapter.")
    parser.add_argument("ebook", type=int, help="Project Gutenberg ebook number, e.g. 13507")
    args = parser.parse_args()
    try:
        manifest = load_manifest(args.ebook)
    except FileNotFoundError as error:
        sys.exit(str(error))
    GUTENBERG_DIR.mkdir(parents=True, exist_ok=True)
    source = GUTENBERG_DIR / f"pg{args.ebook}.txt"
    fetch(URL.format(n=args.ebook), source)
    try:
        chapters = split_chapters(body(source.read_text(encoding="utf-8")), manifest)
    except ValueError as error:
        sys.exit(f"the manifest doesn't fit the text: {error}")
    paths = write_chapters(chapters, GUTENBERG_DIR / str(args.ebook), manifest.authors)
    for path, (_, text) in zip(paths, chapters, strict=True):
        print(f"{path.name}: {len(text.split()):,} words")
    folder = GUTENBERG_DIR / str(args.ebook)
    if manifest.kind in ("poems", "stories"):
        print(
            f"add them: python -m spanish_tutor.content add-{manifest.kind} {folder} "
            f'--collection "{manifest.title}" --author "{manifest.author}" '
            f"--source gutenberg:{args.ebook}"
        )
    else:
        print(
            f"add it: python -m spanish_tutor.content add-book {folder} "
            f'--title "{manifest.title}" --author "{manifest.author}" --source gutenberg:{args.ebook}'
        )


if __name__ == "__main__":
    main()
