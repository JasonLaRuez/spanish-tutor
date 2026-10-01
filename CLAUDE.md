# Spanish tutor: project instructions

Read this file first. It is the durable context for this project — what it is, why it's
shaped the way it is, and the roadmap to build it. The project is scaffolded (see "Working
conventions" for stack and layout); this file is the starting point for a fresh session
picking the project up.

---

## What this is

A word-bank-constrained Spanish learning tool: a set of LLM-backed "skills" (conversation,
lyrics/figurative translation, reading) built around one hard rule — the model tracks what
Spanish vocabulary the user actually knows, and stays inside it, teaching new words on
purpose instead of by accident.

It exists for two reasons at once, and neither is secondary to the other:

1. **Jason wants to learn Spanish**, and specifically wants to fix the passive/active skill
   gap — listening and reading are comparatively easy; producing output (speaking, writing)
   is what actually builds fluency and is what most tools under-serve. This project is
   built to be used, not just built.
2. **It's also a deliberate skill-building project**, designed to close the specific gaps
   found in a macro keyword-gap analysis of 106 real job postings across five title
   families (Data Scientist, Applied/Research Scientist, AI/ML Engineer, Data Analyst,
   Data Engineer) — run in a companion project, `jobhunt-agents`, as part of Jason's active
   job search. See "Why these design choices" below for exactly how each architectural
   decision maps back to a real, frequency-counted gap in that analysis, not a guess.

Both purposes are load-bearing. A design choice that serves the resume story but makes the
tool worse to actually use is a bad trade, and vice versa — the point of the project is that
good design doesn't have to choose.

## The core problem this solves

Passive Spanish content (a song, a graded reader, a canned conversation) is easy to find.
What's missing from most tools:

- **Non-literal translation.** A word-for-word translation of song lyrics or idiom is
  usually useless — you need the natural, idiomatic meaning, which requires retrieval
  grounded in real usage, not just a dictionary lookup.
- **Vocabulary that compounds.** Most tools don't track what you already know, so every
  session starts from zero context and either re-teaches things you have down or throws
  words at you with no scaffolding.
- **Overwhelm control.** Thrown into content with too many unfamiliar words at once, a
  learner disengages. The right next thing to read or talk about is usually the thing that
  needs the *fewest* new words, not the most interesting one in the abstract — though the
  user should always be able to override that and go read/listen to whatever they actually
  want.
- **Early conversations.** Many language learners are hesitant to speak or write in their target 
  language until they feel like they know a vast vocabulary of words - they are afraid of not 
  knowing the words they need to voice their thoughts. By constraining and focusing the vocabulary
  used, conversations can be had almost immediately, building user confidence and fluency efficiently and early.

## Functionality

### Three skills
- **Conversation** — topic-focused (gardening, sports, weather, etc.). At the start of a
  topic conversation, the model identifies the topic's relevant vocabulary, pre-teaches
  whatever isn't already known, logs it to the word bank, *then* starts talking — not
  reactive-only vocabulary teaching mid-conversation.
- **Lyrics / figurative translation** — takes a song and produces a natural, idiomatic
  translation and annotation, not a literal one, grounded in a real idiom/expression corpus.
  Then, produces a literal translation, so the learner can compare the differences and gain
  insights about literal versus natural translations.
- **Reading** — short stories, and full multi-chapter books via sequence-aware chapter
  handling (see below). Same new-word detection and teaching flow as the other two skills.

### The word bank
A persistent, structured store of vocabulary the user has learned: word, part of speech,
date learned, familiarity score, an example sentence. Every skill response reads it before
generating (to constrain vocabulary) and writes to it whenever something new gets taught.
It is the one piece of state everything else in the project revolves around.

Schema decisions (settled with Jason 2026-09-29; the schema lives in `sql/schema.sql`):
- **Words are keyed on `(lemma, pos)`** in `lexemes`. *bajo* ADJ/ADP/NOUN are distinct
  words; POS-tagger mistakes are an accepted cost. spaCy's AUX is folded into VERB at
  ingestion so *ser*/*estar*/*haber* aren't split in two. Multi-word expressions are
  allowed (`pos = 'EXPR'`); likely source is Wiktionary via kaikki.org (CC BY-SA),
  detected in text with spaCy's `Matcher` on lemmas.
- **Recognition and production are tracked separately** (the `mode` column). A word enters
  recognition when taught and production the first time the learner uses it. This is the
  passive/active gap the project exists to close, so it belongs in the data model.
- **`word_events` is an append-only log and the source of truth**, chosen so scores can be
  recalculated when the formula changes and so evaluation has full history.
  `word_bank` is a derived table kept current by a trigger (per-word recompute), because a
  pure view over the log was benchmarked at 6.7 s per single-word lookup at 1M events.
  With the trigger, at 11M events (~a decade of heavy use): 0.1 ms single-word lookup,
  8 ms for the full known-word set, 0.23 ms to log an event, ~3.3 min for a full rebuild.
  `word_bank_rebuild` (view) plus `sql/rebuild_word_bank.sql` recompute it from full history.
  The familiarity formula exists in both the trigger and the rebuild view; a test asserts
  they agree.
- **Single learner.** Generalizing later is expected to mean a `learner_id`; not planned soon.
- Familiarity is a placeholder (mean of the last 5 grades, 0–1, NULL = not yet assessed)
  until Phase 6 replaces it with SM-2.

### Proficiency goals: DELE / SIELE
Part of the tool's purpose is preparing for the DELE or SIELE exams, giving the learner
concrete goals. Levels come from the *Plan Curricular del Instituto Cervantes* (PCIC), which
lists expected grammar, functions and topic vocabulary per level A1–C2 (freely readable
online, but copyrighted: check the license before committing anything extracted from it;
treat it like `private/` until then).
- `lexemes.cefr_level` tags each word with its PCIC level where known.
- Progress is reported as vocabulary coverage per level, split by mode: e.g. "B1:
  recognize 68%, can produce 41%." Jason wants this visualized (e.g. a circular progress
  ring per level). There is no UI in the roadmap yet, so this adds new scope.
- **Always label this as vocabulary readiness, never as a CEFR level.** The exams also test
  grammar, listening and writing.
- PCIC topic vocabulary is a second grounding source for Phase 2 topics, alongside
  Subtlex-ESP. The recommender may use the target level to break ties between equal-cost items.

### Spanish-language definitions
**A Spanish definition is monolingual, not a translation.** The English definition of
*leer* is "to read"; its Spanish definition *explains* the word in other Spanish words, the
way a Spanish dictionary does (e.g. "pasar la vista por un texto para entender lo que
dice"). It is never a translation of the English gloss, a bare synonym, or anything
containing the headword itself (translating "to read" back would just give "leer"). The
known-words check below should therefore reject a definition that uses the headword or any
form of its lemma.

Source is an open decision:
- **Spanish Wiktionary** via kaikki.org (`https://kaikki.org/dictionary/downloads/es/es-extract.jsonl.gz`,
  ~97 MB, mixed languages, so filter `lang_code == "es"`; CC BY-SA). A real, citable
  source, but written for native speakers, so fewer definitions will pass the known-words
  check.
- **Claude-generated, constrained to the learner's word bank**, like a learner's dictionary
  with a restricted defining vocabulary. More would pass, and it's the project's core
  mechanism, but it isn't grounded in a real source.

Definitions switch from English to Spanish per word, not at a global level threshold. When a
word is defined, take its Spanish definition, lemmatize it, and check each word against the
learner's *recognition* word bank (the same new-word check the recommender uses):
- Every word known → show the Spanish definition.
- Exactly one word U unknown → U is taught. Show U's definition first: in Spanish if every
  word in *U's* Spanish definition is known, otherwise in English. Then **always** show the
  original word's Spanish definition, which is now fully comprehensible.
- Two or more unknown → show the original word's English definition.
The check goes one level deep only, so definitions never recursively teach new words and
overwhelm the learner. `lexemes.definition_es` caches the generated definition; the
known-words check runs at display time because it depends on the current word bank. The
share of definitions given in Spanish over time is a natural growth metric. For other
interface text (instructions, feedback), a vocabulary-level threshold is the likely
default; an explicit user override always wins.

### The recommender (comprehensible-input engine)
Given the word bank and a set of candidate content items (topics, songs, stories, book
chapters), rank them by how many *new* words each one would require, and default to the
minimum — unless the user explicitly asks for something specific, which always wins. This
is a direct implementation of Stephen Krashen's **comprehensible input / i+1 hypothesis**:
learners acquire language best from input just slightly above their current level, not from
being overwhelmed or under-challenged. It is the single most novel piece of the system —
most language-learning tools don't do this at all.

Multi-chapter books get one extra constraint here: chapters of the same book must stay in
reading order. The recommender can rank *which* book to start or continue against other
candidates by vocabulary cost, but it must never suggest chapter 7 before chapter 6 of the
same book. When a book is started, the next chapter of that book should always be recommended
before the start of a different book is recommended. This is to ensure book reading continuity.

### Content-difficulty index
Every corpus item (song, story, chapter, topic vocabulary set) gets its full vocabulary set
indexed at ingestion time, so the recommender's ranking is a fast lookup rather than a live
recomputation. This is also what turns "occasionally add new content" into a real,
schedulable job (see Orchestration below).

## Why these design choices

Each major architectural decision exists because it closes a specific, frequency-counted
gap from the `jobhunt-agents` macro analysis — not because it sounded good in isolation.

| Choice | Job-market gap it closes | Frequency |
|---|---|---|
| The whole system is retrieval-augmented generation | LLM / RAG | 42–63% of Data Scientist, Applied/Research Scientist, and AI/ML Engineer postings |
| Containerized, deployed behind a real endpoint | MLOps / production deployment | 33–47% of the same three title families |
| Word bank + vector store + difficulty index all hosted on a named cloud platform (Databricks or AWS) | Cloud / modern data platform | The widest gap found — present in **5 of 5** title families, 45–55% of Data Engineer specifically |
| Two real scheduled jobs (spaced-repetition recalculation, content-difficulty re-indexing) | Orchestration (Airflow/dbt) | 36% of Data Engineer |
| Word bank schema, the recommender's ranking query, and every dbt model written as real SQL | Not one of the four gaps above — SQL was already claimed as "working knowledge," this project is what makes that claim defensible | 75% of Data Analyst postings, common in Data Engineer |

A few choices exist for reasons specific to this project rather than the macro analysis:

- **The evaluation framework is custom, not just RAGAS.** Generic RAG evaluation (RAGAS's
  faithfulness/relevance metrics) doesn't capture what this app actually needs to prove.
  RAGAS is used as a baseline grounding check on the grammar/idiom retrieval, but the real
  metrics — vocabulary-constraint adherence, new-word teaching completeness, translation
  naturalness, recommendation quality — are designed from scratch. This mirrors the
  dissertation's own discipline of defining and defending a metric rather than importing
  someone else's, and is a stronger portfolio story than "I ran an off-the-shelf eval."
- **Tatoeba as the primary corpus.** CC-licensed, community-built, explicitly designed for
  language learners, and — importantly — legally clean to put in a public repo, unlike
  almost every other plausible Spanish-language source.
- **Subtlex-ESP for topic vocabulary**, instead of just asking the model to guess what
  words are "relevant to gardening." Check a claim against a real source before teaching it as fact.
- **Project Gutenberg for the reading skill's public-facing content.** Solves the reading
  skill's copyright question outright — explicitly public domain, no ambiguity — rather
  than needing a private/public split the way lyrics do.
- **Song lyrics, and any copyrighted book, are never committed to the public repo.**
  Copyright applies to the content itself, not its chunking — splitting a book into
  chapters changes its architecture, not its license. Keep copyrighted material local and
  gitignored from the start; the public repo ships with Gutenberg texts and public-domain
  poetry as the demo equivalent.

## Architecture

Three loops, two of which share the same underlying tables:

**1. Content pipeline** (mostly static, refreshed occasionally)
`Corpora (Tatoeba, Gutenberg, curated lyrics/topics) → chunk + embed (by chapter for books) → vector store (Chroma locally, migrates to the cloud platform) → difficulty index (vocab set per item)`

**2. Recommend + pre-teach** (runs before a skill starts)
`Word bank + difficulty index → recommender (fewest new words; book chapters stay in sequence) → selected content (or an explicit user request, which always wins) → pre-teach new words → word bank updated`

**3. Skill loop** (runs during the interaction)
`Word bank → skill (conversation / lyrics / reading) → response (new words taught + defined) → word bank updated`

The word bank and difficulty index are the same two tables throughout, not separate stores
per stage — everything reads and writes against shared state.

Wrapped around all three loops:
- An **orchestrator** (dbt or Airflow) runs the spaced-repetition recalculation against the
  word bank on a schedule, and separately re-indexes content difficulty whenever new corpus
  items are added.
- A **deployment layer** (Docker + FastAPI, hosted on the cloud platform) puts all three
  skills and the recommender behind a real endpoint instead of a notebook cell.

## Roadmap

~10–12 weeks, part-time. Full detail (including per-phase checklists with saved progress)
lives in the published roadmap artifact — ask Jason for the link, or rebuild it from this
file if it's been lost. Phase structure:

| Phase | Focus |
|---|---|
| 0 — Learning sprint (week 1) | LangChain/RAG, vector-DB, and prompt-engineering fundamentals. No building yet. |
| 1 — Word bank + basic conversation skill (weeks 2–3) | Prove the core loop locally: SQL schema (`CREATE TABLE`, not an ORM), seed vocabulary, Tatoeba into local Chroma, a conversation skill constrained to word-bank vocabulary, write-back confirmed. |
| 2 — Topic focus + pre-teaching (weeks 3–4) | Starter topics, vocabulary grounded against Subtlex-ESP, pre-teach flow before conversation starts. |
| 3 — Content-difficulty index + recommender (weeks 4–6) | The novel subsystem: difficulty-index schema, the "fewest new words" ranking as a real SQL join-and-aggregate query, the book-sequence constraint, the explicit-request override. |
| 4 — Lyrics + reading skills, and evaluation (weeks 6–8) | Both remaining skills, a multi-chapter book with chapter-level difficulty indexing, RAGAS as a baseline check, then the custom evaluation metrics and a written case study. |
| 5 — Cloud platform (weeks 8–9) | Migrate the vector store, word bank, and difficulty index onto Databricks (or AWS) while working through the platform course. |
| 6 — Orchestration (weeks 9–10) | Spaced-repetition recalculation and content re-indexing as scheduled dbt models (or Airflow DAGs) — SQL-first, not Python wrapping raw SQL. |
| 7 — Deployment (weeks 10–11) | FastAPI + Docker, a real endpoint, basic observability, and a handful of analytics queries (window functions) over the logs. |
| 8 — Polish & publish (weeks 11–12) | Public repo (copyrighted material gitignored), architecture note, written case study, and only then — honest resume bullets and a new cover-letter evidence block. |

### Objectives (what "done" means)
1. Ship a tutor with all three skills that Jason would actually keep using afterward.
2. A real, persistent word bank read and written on every interaction.
3. A working comprehensible-input recommender (Krashen's i+1), with an explicit-request
   override.
4. A custom evaluation framework — vocabulary adherence, teaching completeness, translation
   naturalness, recommendation quality — validated with real numbers.
5. A containerized deployment behind a real endpoint with basic observability.
6. Two real scheduled jobs running against real state.
7. A public, documented artifact with copyrighted content kept private.
8. Real, demonstrable SQL throughout — not just a claim on a resume.

### Honesty checkpoints 
- **Never commit copyrighted material** (song lyrics, any book still under copyright) to a
  public repo — chunking it by chapter doesn't change its license. Keep it local and
  gitignored from the start, not cleaned up retroactively.

## Working conventions

- **Status (as of 2026-10-01):** Phase 0 is complete. Phase 1 has 3 of 7 roadmap steps done,
  plus extra groundwork:
  - Done: the word bank schema; the seed (987 recognized / 730 produced words); Tatoeba in
    Chroma (261k sentences).
  - Extra: lemma correction, and the general lexicon (26k words).
  - **Next: the conversation skill**, then write-back of taught words, then ~20 real
    conversations.
  - The API key is in `.env` (gitignored) and authenticates, but the account had **no
    credits** on 2026-10-01. Jason needs to add credits (Console → Plans & Billing) before
    any LLM call.
  - Build log, published (private): https://claude.ai/artifact/NAheU5cpSBdxY7QpoeQbHQ.
    Republish it after milestones. The roadmap artifact's link is still unknown; ask Jason.
- **Open design points for the conversation skill** (settle with Jason before building):
  - **The loop:** retrieve with `vectorstore.search_sentences(query, db.known_vocabulary(conn),
    max_unknown=0|1)`; generate with Claude, constrained to known words; detect new words
    with `lexicon.analyze` + `vocabulary()`; teach them; log events.
  - **Logging:** a `taught` event teaches a word already in `lexemes`. Words *not* in the
    lexicon (absent from Tatoeba) still need an on-demand `ensure_lexeme(lemma, pos)`
    (Wiktionary definition, rejecting non-words), and that isn't built yet.
  - **Examples at teach time:** re-choose them against the learner's word bank. For rare
    words with no stored example, use the sentence where the word was met.
  - **Grading policy is undecided.** What grade does a `seen`, `looked_up` or `used` event
    get, and who grades production: Claude as judge, or rules?
  - **Model settings:** `claude-opus-5-5` rejects `temperature` and can't disable thinking;
    its effort defaults to `medium` (set it explicitly; `low` likely suits chat turns). Use
    prompt caching for the system prompt and vocabulary. Check how `langchain-anthropic`
    exposes `output_config.effort` before relying on it.
  - **Adherence check:** the Spanish output should be checked against the word bank after
    generation (it's also a Phase 4 metric). Decide whether a violation triggers a retry or
    gets taught as a new word.
- **Known data limits:** *ven* resolves to *ver* by frequency (often the imperative of
  *venir*); sentence-initial words are sometimes tagged as names and dropped; ~100
  NOUN-tagged *conmigo* tokens became an ADV entry; multi-word expressions (`EXPR`) are
  allowed in the schema but not yet detected in text.
- **Notebook:** `notebooks/01_data_pipeline.ipynb` is now the source; edit it directly. The
  generator script used to create it was temporary and no longer exists.
  - Verify edits with
    `uv run jupyter nbconvert --to notebook --execute --inplace notebooks/01_data_pipeline.ipynb`
    and keep `ruff check` passing; ruff lints notebooks.
  - Outputs are stripped from commits by an nbstripout git filter. On a fresh clone, run
    `uv run nbstripout --install --attributes .gitattributes`, because the filter lives in
    local git config.
- **Working with Jason:** commit only when he asks (he usually does after each milestone).
  Use plan mode for multi-step work when he requests a plan. Publish-worthy summaries go to
  the build log artifact.
- **Decided stack:** Python 3.12 managed with `uv` (`uv sync`, `uv run pytest`,
  `uv add <pkg>`). LLM is the Claude API via LangChain (`langchain-anthropic`); LangChain was
  chosen deliberately because Jason already knows it and it is frequently named in DS/AI
  job postings. Default model is `claude-opus-5-5`, set in `src/spanish_tutor/config.py`
  (overridable with `TUTOR_MODEL`). That model rejects `temperature`/`top_p`/`top_k` and
  can't disable thinking, so don't pass sampling params to `ChatAnthropic`. Local vector
  store is Chroma (`langchain-chroma`); the word bank is SQLite, written as raw SQL.
- **Layout:** `sql/` (schema, seeds, hand-written queries), `src/spanish_tutor/` (package),
  `data/raw/` and `data/processed/` (gitignored corpora, DB, Chroma store), `private/`
  (gitignored copyrighted lyrics/books), `tests/`. `tests/test_repo_hygiene.py` asserts
  that `private/` and `.env` stay gitignored. Keep it passing. `src/spanish_tutor/db.py`
  opens connections (always with `PRAGMA foreign_keys = ON`) and applies `sql/schema.sql`.
- **All text → `(lemma, pos)` goes through `src/spanish_tutor/lexicon.py`** (spaCy
  `es_core_news_md`: NFC, lowercase, accents kept, AUX→VERB, `del`/`al` expanded, clitic
  verbs reduced to the verb, and **personal pronouns keep their own form**, because spaCy
  would otherwise merge *me/nos/conmigo* into *yo* and *se/lo/le* into *él*). Phase 3
  content indexing must reuse it, or the word bank and difficulty index disagree about
  what's "new".
- **Lemma correction** (`lexicon.LemmaCorrector`, added 2026-10-01):
  - When spaCy's `(lemma, pos)` isn't a Wiktionary word, it's repaired from Wiktionary's
    form-of table (`data/raw/wiktionary_es_forms.tsv`).
  - Examples: *has* → *haber*, *deberías* (spaCy: *deberiar*) → *deber*, *dólares* → *dólar*,
    *déjame* → *dejar*, and a wrongly tagged *serio* NOUN → *serio* ADJ.
  - Ties between candidate lemmas use SUBTLEX frequency (*crees* → *creer*, not *crear*);
    remaining ties are never guessed.
  - Every Tatoeba run writes `data/processed/lemma_corrections.csv` for review. The first run
    corrected ~88k tokens; ~0.6% of tokens stay unresolved (truly ambiguous: *tal*, *solo*,
    *siquiera*).
  - Analyzing text therefore needs the downloaded Wiktionary files. Tests pass
    `corrector=None` for raw spaCy behavior.
  - Known tagger limits the corrector can't fix: sentence-initial words are sometimes
    tagged as proper nouns (*Sales a las ocho*) and dropped.
- **Wiktionary matching** (`ingest/wiktionary.py`):
  - Form-of senses are dropped for open-class words (only dictionary forms remain), but kept
    for function words (*ti* = "prepositional of tú") and irregular comparatives (*peor*,
    *mejor*, *mayor*, *menor*), which are separate words to learn.
  - Function words may match across compatible POS (spaCy's PRON *cómo* ↔ Wiktionary's
    adverb), always keeping spaCy's POS.
  - Accepted consequence of `(lemma, pos)` keys: tagger-inconsistent function words appear
    under several POS (*mismo* DET/ADJ/PRON).
- **General lexicon** (`ingest/build_lexicon.py` + `sql/fill_lexicon.sql`, added 2026-10-01):
  - Contents: every `(lemma, pos)` in the lemmatized Tatoeba corpus that Wiktionary knows,
    with `frequency_per_million`, an English definition, and a learner-independent example.
    The example is the most readable sentence for someone knowing the 2,000 most frequent
    words; it is re-chosen per learner when a word is taught.
  - Independent of any learner, and rebuildable by anyone with one command (Jason's reason
    for choosing an advance fill over on-demand creation).
  - **Additive only:** rows are never deleted, so `lexeme_id`s and the events referencing
    them survive rebuilds. Frequencies are overwritten; definitions and examples only fill
    blanks (examples move as a unit).
  - The database is backed up before each build (`db.backup`).
  - `seed candidates` ranks this lexicon; `seed build` only records events.
  - **Example coverage (measured 2026-10-01): 70% overall, but concentrated where it
    matters.**

    | Frequency rank | Have an example |
    |---|---|
    | Top 2,000 | 100% |
    | 2,001–5,000 | 99.1% |
    | 5,001–10,000 | 92.3% |
    | Beyond 10,000 | 64.5% |
    | No subtitle frequency | 39.3% |

    Missing examples are words that occur only in untranslated Tatoeba sentences.
  - **Decided: no Wiktionary usage examples.** Only 461 of the 7,860 example-less words have
    a translated one (+1.8 points). Over half of those are long literary quotations, some
    with a book title in place of the translation.
  - **Decided: for the rare tail, use the sentence where the learner actually met the word**
    when it is taught. Build this into the conversation skill's teach step.
  - **Later, with the LLM review pipeline:** translate the untranslated Tatoeba sentences with
    Claude (~7.9k short sentences, a few dollars with Haiku via the Batch API). This needs
    a schema decision first: record that the English is model-generated, e.g. an
    `example_en_source` column.
- **Planned: LLM accuracy review** of the lexicon (definitions, examples, translations)
  with a bilingual Spanish–English model, Jason's idea:
  - Results go in `lexeme_reviews`: append-only, never overwriting `lexemes`, with the
    reviewer recorded.
  - Applying accepted fixes is a separate step that updates `definition_source`
    (e.g. `wiktionary+reviewed`).
  - Belongs with Phase 4 evaluation (LLM-as-judge). Model choice and budget are Jason's.
    Rough Batch API cost for ~30k entries: ~$5–10 with Haiku, ~$50 with Opus.
- **Schema migrations:** `schema.sql` is the latest schema, for new databases. Every change
  to an existing table also needs a numbered file in `sql/migrations/`; `db.init_schema`
  applies pending ones, tracked in `PRAGMA user_version`. Never edit an applied migration.
- **Data licensing rule:** the repo ships code, never data. SUBTLEX-ESP is CC BY-NC-SA 4.0,
  Wiktionary (kaikki.org) CC BY-SA, Tatoeba CC BY 2.0 FR with per-sentence author credit
  (stored in `lexemes.example_source`/`example_author`), and the spaCy model is GPL-3.0.
  Everything downloaded or derived lives in `data/raw/` and `data/processed/` (gitignored,
  enforced by `tests/test_repo_hygiene.py`). Credits are in the README.
- **Seed pipeline** (`seed.py`, commands in the README): the ~1,500 most frequent lexicon
  words are written to a CSV. Jason marks each `r` (recognize) or `p` (can produce), and
  `seed build` records the marks as `seed` events via a re-runnable, set-based SQL script.
  Frequencies come from SUBTLEX form counts split across `(lemma, pos)` by how each form
  is used in Tatoeba.
- **Embeddings** (`embeddings.py`, decided 2026-09-30):
  - Model: `jinaai/jina-embeddings-v2-base-es` (Jina AI, Apache-2.0, Spanish–English, 768-dim,
    8,192-token context), via LangChain's `HuggingFaceEmbeddings`.
  - Hardware: PyTorch on CPU, which was Jason's choice. PyTorch is a wanted DS-job skill, and
    the GTX 1080 is too old for current CUDA wheels.
  - Why jina over `multilingual-e5-base`: similar speed, far better separation. Related vs.
    unrelated similarity was 0.895 vs. 0.038, where e5 gave 0.92 vs. 0.84, so jina's scores
    are usable as thresholds.
  - Cost of that choice: its remote code (`trust_remote_code`) imports `transformers.onnx`,
    which transformers 5 removed. **`transformers<5` and `sentence-transformers<6` are pinned
    for this reason; don't upgrade them without re-checking the model.**
  - The model repo and its code repo are pinned to exact commit SHAs (`MODEL_REVISION`,
    `CODE_REVISION`).
  - Speed: ~130 sentences/s on CPU, so the full Tatoeba index takes ~35 min.
- **Vector store** (`vectorstore.py`):
  - Chroma collection `tatoeba` in `data/processed/chroma/`, cosine distance.
  - One document per translated sentence, id `tatoeba:<id>`. Metadata holds `en`, `author`
    and `vocab`, the sentence's `(lemma, pos)` set from `lexicon.py`, encoded
    `"lemma|POS;…"` because Chroma metadata must be scalar.
  - The collection records its embedding model and revision, and `open_store()` refuses a
    mismatch; switching models means deleting the store and re-indexing.
  - `search_sentences(query, known, max_unknown=…)` over-fetches by similarity, then keeps
    sentences with at most `max_unknown` words outside the word bank (0 = fully
    comprehensible, 1 = i+1). It reports the unknown words so the skill can pre-teach them.
  - The vocab filter is a Python post-filter for now; Phase 3's SQL difficulty index is its
    long-term home.
- **Tests:** tests marked `slow` load the real embedding model and are excluded by default.
  Run them with `uv run pytest -m slow` after touching embeddings or their pins.
- **Schema workflow:** the schema is designed *together with* Jason, not handed to him. He
  needs to be able to defend every line of the SQL in an interview. Propose and explain;
  let him decide.
- Treat the phase order above as the intended build order unless Jason says otherwise — it
  exists specifically so later phases (recommender, cloud migration, orchestration) build on
  working, tested earlier ones rather than everything landing at once.
- If scope questions come up that aren't answered here (e.g., which specific topics to seed
  first, which LLM API to call, exact hosting choice within "Databricks or AWS"), ask rather
  than assume — several of these were left as open choices deliberately in the roadmap
  rather than decided prematurely.
- When implementing features, first construct a detailed plan of what will be done, and how it will be implemented.
- When features are implemented, propose a suite of test cases to ensure that the project can be methodically debugged when things go wrong.
- Use Git for version control. This project will be a local repo, containing potentially copyrighted materials, and a public repo, where only open-access materials will be available (git ignore copyright content). When minor features are implemented, create a commit to the local repo with a message describing the changes - show the commit message to the user and ask for feedback before doing the commit. When major features are completed, ask the user if they would like the repo to be pushed to the publicly available repo. The public repo is important, as it will be referred to in job applications and interviews.
