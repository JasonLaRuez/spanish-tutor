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

## Building the lexicon and seeding your word bank

All data is downloaded and generated locally; none of it is committed. The first three
steps build a general Spanish lexicon (about 30k words with frequencies, definitions and
example sentences) that doesn't depend on any learner. The last two record which of
those words *you* know.

```sh
uv run python -m spanish_tutor.ingest.download        # corpora into data/raw/ (a few minutes)
uv run python -m spanish_tutor.ingest.tatoeba         # lemmatize Tatoeba once (~1 h on CPU)
uv run python -m spanish_tutor.ingest.build_lexicon   # the general lexicon (backs up the DB first)
uv run python -m spanish_tutor.seed candidates        # ~1,500 most frequent words to review
# Open data/processed/seed_candidates.csv and fill the `known` column:
#   r = I recognize it, p = I can also use it myself, blank = don't know it
uv run python -m spanish_tutor.seed build        # load the marked words into the word bank
```

## Example-sentence search

Tatoeba sentences are embedded into a local Chroma store so the tutor can retrieve
real examples by meaning, keeping only ones you can read.

```sh
uv run python -m spanish_tutor.ingest.index_tatoeba          # ~35 min on CPU; resumable
uv run python -m spanish_tutor.ingest.index_tatoeba --update-vocab   # after re-lemmatizing
uv run python -m spanish_tutor.vectorstore "the weather"     # only words you know
uv run python -m spanish_tutor.vectorstore "I'm hungry" --max-unknown 1   # one new word (i+1)
```

## The web app

```sh
uv run spanish-tutor serve        # http://127.0.0.1:8000 (API docs at /docs)
```

The browser UI: start a conversation (with a topic and how many words to learn first), chat
in a chat box with clickable words, an accent keyboard and a "¿Cómo se dice…?" button,
browse past conversations, and see your progress (words you recognize and can produce,
coverage by word frequency, growth per conversation, and words to try using). It serves the
built UI from `web/dist`; build it once with Node.js installed:

```sh
cd web && npm install && npm run build
```

For UI development, run `npm run dev` in `web/` alongside `serve` (hot reload on
http://localhost:5173). The app listens on this machine only by default. Everything below
also works from the terminal.

## Conversation

Chat with Claude in Spanish, inside the vocabulary you know. Each reply may use at most
one word outside your word bank (two or more trigger a single rewrite). Any new word is
taught with a definition and an example sentence you can read. The words you use
yourself are credited to your production vocabulary, and a mistake gets a gentle recast
plus a short note in English.

```sh
uv run python -m spanish_tutor.conversation --topic "el tiempo"
```

With a topic (from `--topic`, or asked at the start), the tutor first teaches topic words
(you choose how many, 2–20), then asks questions that invite you to use them. The candidates
come from the 2,000 Tatoeba sentences closest to the topic, ranked by how strongly each word
is associated with it; Claude picks the most useful ones from that list and can't add others.
A broad topic may get fewer words than you asked for, and you're told why. In the web app,
"Today's words" checks each one off as you use it.

During a conversation:

| Type | To |
|---|---|
| `¿Cómo se dice "..."?` | Pause the conversation and ask how to say something in Spanish. Every new word in the answer is taught. |
| `/q palabra` | Look a word up |
| `/en` | See the last reply in English |
| `/palabras` | List the words taught this session |
| `/salir` | End the conversation and see its summary |

To end a conversation, say goodbye: a message ending with *hasta luego*, *adiós*, *chao*,
*nos vemos* (and similar) ends it, or use the web app's **¡Hasta luego!** button. You get a
summary: minutes, messages, corrections, words used and taught, the words you used for the
first time ever, which of today's words you used, and the tutor's notes on what went well
and what to work on next. History keeps each summary.

On an English keyboard, type accents as markers before the letter: `'a` → á (any vowel),
`~n` → ñ, `:u` → ü, and `?` or `!` directly before a word → ¿ or ¡. So `?Qu'e tal?`
becomes `¿Qué tal?`. Words typed without any accents are also recognized (`manana`,
`detras`) when only one Spanish word fits.

Needs `ANTHROPIC_API_KEY` in `.env`; a turn costs about a cent. Every turn is logged in
the `sessions` and `turns` tables, and each word event links to the turn that caused it.

## Tests

`uv run pytest` runs the fast suite. `uv run pytest -m slow` also loads the real embedding
model, and `uv run pytest -m live` calls the Claude API (two turns, about a cent). The web
UI's component tests run with `npm test` in `web/`.

## Layout

| Path | Contents |
|---|---|
| `sql/` | Schema (`CREATE TABLE`), and hand-written queries |
| `src/spanish_tutor/` | Python package: word bank access, ingestion, skills, recommender |
| `src/spanish_tutor/api/` | The web API (FastAPI) |
| `web/` | The web UI (React + TypeScript, Vite) |
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
- **spaCy** `es_dep_news_trf` for lemmatization and part-of-speech tagging (GPL-3.0;
  installed as a dependency, not bundled). It is built on BETO,
  [`dccuchile/bert-base-spanish-wwm-cased`](https://huggingface.co/dccuchile/bert-base-spanish-wwm-cased)
  (Cañete et al. (2020). Spanish Pre-Trained BERT Model and Evaluation Data.
  *PML4DC at ICLR 2020*). CC BY 4.0.
- **Jina AI** [`jina-embeddings-v2-base-es`](https://huggingface.co/jinaai/jina-embeddings-v2-base-es)
  for sentence embeddings (Apache-2.0; downloaded to the Hugging Face cache, not bundled).

Song lyrics and copyrighted books are supported as local input under `private/` and
never leave your machine.
