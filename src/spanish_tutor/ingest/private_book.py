"""A book you own, from a PDF with selectable text, split into chapter files for add-book.

    uv run python -m spanish_tutor.ingest.private_book "private/books/Libro.pdf"
    uv run python -m spanish_tutor.content add-book "private/books/Libro" \\
        --title "..." --author "..." --source private --private

Everything stays in private/ (gitignored): the PDF, and the chapter files written into a
folder named after it, with chapters.json holding each chapter's exact title (as
ingest.gutenberg writes it). Nothing here is committed but the code.

How the text is rebuilt, measured on the first book (a 264-page PDF with no bookmarks):
- Chapters start at a line "CAPÍTULO n" (accent optional; a number or roman numeral), and
  the line(s) in capitals right after it are its title. Front matter before the first
  chapter (a synopsis, credits, the index) is left out. The split fails loudly if no
  chapter is found, rather than guess.
- Paragraphs are separated by blank (whitespace-only) lines in the extracted text; the
  lines inside one are hard-wrapped, which content.sentences rejoins.
- A page break ends a paragraph only when the page's last line looks like one's end: it
  ends a sentence and is shorter than a full line. Otherwise the paragraph runs on.
- A word hyphenated across lines ("pala-" / "bra", a lowercase continuation) is joined;
  a dialogue dash (Spanish "-dijo") is left alone.
- A credit line under a heading ("Traducido por ...", "Transcrito por ...") is dropped.
- Titles in capitals are put in sentence case; a word keeps its capital where the book
  itself writes it capitalized mid-line more often than not (names: Percy, Nueva York).
"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

from spanish_tutor.ingest.gutenberg import write_chapters

CHAPTER = re.compile(r"^\s*CAP[IÍ]TULO\s+(\d+|[IVXLC]+)\.?\s*$", re.IGNORECASE)
CAPS = re.compile(r"^[^a-záéíóúñü]*[A-ZÁÉÍÓÚÑÜ][^a-záéíóúñü]*$")
# A credit line under a heading (fan translations credit each chapter): not text.
CREDIT = re.compile(
    r"^\s*(?:Traducido|Transcrito|Corregido|Revisado|Traducción)\s+(?:por|de)\b.*$",
    re.IGNORECASE,
)
SENTENCE_END = re.compile(r"[.!?…»\"”]\s*$")


def join_pages(pages: list[str]) -> str:
    """The pages as one text: blank lines between paragraphs, a page break joining a
    paragraph that runs on to the next page."""
    lines_all: list[str] = []
    widths = [len(ln.rstrip()) for page in pages for ln in page.splitlines() if ln.strip()]
    full = sorted(widths)[int(len(widths) * 0.75)] if widths else 0  # a typical full line
    for page in pages:
        lines = [ln.rstrip() for ln in page.splitlines()]
        while lines and not lines[-1].strip():
            lines.pop()
        while lines and not lines[0].strip():
            lines.pop(0)
        if not lines:
            continue
        if lines_all and lines_all[-1].strip():
            last = lines_all[-1]
            ends_paragraph = SENTENCE_END.search(last) and len(last) < full * 0.9
            if ends_paragraph or CHAPTER.match(lines[0]):
                lines_all.append("")
        lines_all.extend(lines)
    text = "\n".join("" if not ln.strip() else ln for ln in lines_all)
    # pala-\nbra -> palabra (a lowercase continuation only: not a dialogue dash).
    return re.sub(r"(\w)-\n([a-záéíóúñü])", r"\1\2", text)


def sentence_case(caps: str, words: Counter) -> str:
    out = []
    for w in re.split(r"(\W+)", caps.lower()):
        if not w or not w[0].isalpha():
            out.append(w)
            continue
        cap = w[0].upper() + w[1:]
        first = not "".join(out).strip(' ¡¿!?.,;:()«»"-')
        out.append(cap if first or words[cap] > words[w] else w)
    return "".join(out)


def split_book(text: str) -> list[tuple[str, str]]:
    """(title, text) per chapter: from each CAPÍTULO line to the next."""
    lines = text.split("\n")
    starts = [i for i, ln in enumerate(lines) if CHAPTER.match(ln)]
    if not starts:
        raise ValueError("no chapter heading found (a line 'CAPÍTULO n')")
    # Mid-line capitalization in the book itself, for the titles' names.
    words = Counter(w for ln in lines for w in re.findall(r"\w+", ln)[1:])
    chapters = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        body = lines[start + 1 : end]
        title_lines = []
        body = [ln for ln in body if not CREDIT.match(ln)]
        while body and (not body[0].strip() or CAPS.match(body[0].strip())):
            if body[0].strip():
                title_lines.append(body[0].strip())
            elif title_lines:
                body.pop(0)
                break
            body.pop(0)
        number = CHAPTER.match(lines[start]).group(1)
        title = sentence_case(" ".join(title_lines), words) if title_lines else f"Capítulo {number}"
        chapters.append((title, "\n".join(body).strip()))
    return chapters


def read_pdf(path: Path) -> list[str]:
    from pypdf import PdfReader

    return [page.extract_text() or "" for page in PdfReader(path).pages]


def main() -> None:
    parser = argparse.ArgumentParser(description="Split a PDF book you own into chapter files.")
    parser.add_argument("pdf", type=Path, help="a PDF with selectable text, under private/")
    args = parser.parse_args()
    if "private" not in args.pdf.resolve().parts:
        sys.exit("Keep books you own under private/ (gitignored).")
    pages = read_pdf(args.pdf)
    if not any(p.strip() for p in pages):
        sys.exit("No text in this PDF: it's scanned images, which would need OCR.")
    try:
        chapters = split_book(join_pages(pages))
    except ValueError as error:
        sys.exit(str(error))
    folder = args.pdf.with_suffix("")
    paths = write_chapters(chapters, folder)
    for path, (_, text) in zip(paths, chapters, strict=True):
        print(f"{path.name}: {len(text.split()):,} words")
    print(
        f'add it: python -m spanish_tutor.content add-book "{folder}" --title "..." '
        '--author "..." --source private --private'
    )


if __name__ == "__main__":
    main()
