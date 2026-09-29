# spanish-tutor

A word-bank-constrained Spanish tutor. It tracks the vocabulary you actually know,
generates conversation, lyrics translations, and reading material inside that
vocabulary, and teaches new words on purpose. A comprehensible-input recommender
(Krashen's i+1) suggests whichever content needs the fewest new words.

Early development: see `CLAUDE.md` for the design and roadmap.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync                  # creates .venv with Python 3.12 and all dependencies
cp .env.example .env     # then add your ANTHROPIC_API_KEY
uv run pytest
```

## Layout

| Path | Contents |
|---|---|
| `sql/` | Schema (`CREATE TABLE`), seed data, and hand-written queries |
| `src/spanish_tutor/` | Python package: word bank access, ingestion, skills, recommender |
| `data/raw/` | Downloaded corpora (Tatoeba, Subtlex-ESP, Gutenberg). Gitignored. |
| `data/processed/` | Local SQLite database and Chroma store. Gitignored. |
| `private/` | Song lyrics and copyrighted books. **Gitignored; never committed.** |
| `tests/` | pytest suite |

## Content licensing

Only openly licensed or public-domain content (Tatoeba, Project Gutenberg) is
distributed with this repo. Song lyrics and copyrighted books are supported as
local input under `private/` and never leave your machine.
