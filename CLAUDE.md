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

- **Status:** Phase 0 (learning sprint) is complete. Project scaffolded 2026-09-29; Phase 1
  is next.
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
  that `private/` and `.env` stay gitignored. Keep it passing.
- **Open design points for Phase 1:** (1) the word bank should be keyed on the
  lemma/dictionary form (e.g. *hablar*, not *hablamos*), and content vocabulary lemmatized
  the same way (likely spaCy `es_core_news_*`), or the Phase 3 ranking query miscounts;
  (2) the schema should anticipate SM-2 spaced repetition (ease, interval, next review,
  review history) so Phase 6 doesn't force a migration; (3) Claude has no embeddings
  endpoint, so pick a multilingual embedding model for Chroma (Chroma's default is
  English-centric).
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
