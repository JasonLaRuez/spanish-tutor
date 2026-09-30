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

## Seeding your word bank

The word bank starts from vocabulary you already know. All data is downloaded and
generated locally; none of it is committed.

```sh
uv run python -m spanish_tutor.ingest.download   # corpora into data/raw/ (the Wiktionary step is slow)
uv run python -m spanish_tutor.ingest.tatoeba    # lemmatize Tatoeba once (a few minutes)
uv run python -m spanish_tutor.seed candidates   # ~1,500 frequency-ranked words to review
# Open data/processed/seed_candidates.csv and fill the `known` column:
#   r = I recognize it, p = I can also use it myself, blank = don't know it
uv run python -m spanish_tutor.seed build        # load the marked words into the word bank
```

## Layout

| Path | Contents |
|---|---|
| `sql/` | Schema (`CREATE TABLE`), and hand-written queries |
| `src/spanish_tutor/` | Python package: word bank access, ingestion, skills, recommender |
| `data/raw/` | Downloaded corpora (Tatoeba, SUBTLEX-ESP, Wiktionary). Gitignored. |
| `data/processed/` | Local SQLite database, analyzed corpora, seed files. Gitignored. |
| `private/` | Song lyrics and copyrighted books. **Gitignored; never committed.** |
| `tests/` | pytest suite |

## Data sources and licensing

The repository contains code only. Data is fetched at build time from these sources,
under their own terms:

- **SUBTLEX-ESP**: Cuetos, F., Glez-Nosti, M., Barbón, A., & Brysbaert, M. (2011).
  SUBTLEX-ESP: Spanish word frequencies based on film subtitles. *Psicológica, 32*,
  133–143. [osf.io/xp6sz](https://osf.io/xp6sz/). CC BY-NC-SA 4.0.
- **Tatoeba**: example sentences and translations from [tatoeba.org](https://tatoeba.org),
  CC BY 2.0 FR. Each example is stored with its sentence id and author for attribution.
- **Wiktionary**: English definitions via [kaikki.org](https://kaikki.org) (Ylonen, T.
  (2022). Wiktextract: Wiktionary as Machine-Readable Structured Data. *LREC 2022*).
  CC BY-SA 4.0.
- **spaCy** `es_core_news_md` for lemmatization and part-of-speech tagging (GPL-3.0;
  installed as a dependency, not bundled).

Song lyrics and copyrighted books are supported as local input under `private/` and
never leave your machine.
