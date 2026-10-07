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
  allowed (`pos = 'EXPR'`): a reviewed list from Wiktionary via kaikki.org (CC BY-SA),
  detected in text on lemma sequences (see "Multi-word expressions" below).
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
lives in the roadmap page, committed as `rag-deployment-roadmap.html` (a published
artifact copy may also exist; ask Jason for its link). Phase structure:

| Phase | Focus |
|---|---|
| 0 — Learning sprint (week 1) | LangChain/RAG, vector-DB, and prompt-engineering fundamentals. No building yet. |
| 1 — Word bank + basic conversation skill (weeks 2–3) | Prove the core loop locally: SQL schema (`CREATE TABLE`, not an ORM), seed vocabulary, Tatoeba into local Chroma, a conversation skill constrained to word-bank vocabulary, write-back confirmed. |
| 2 — Topic focus + pre-teaching (weeks 3–4) | Starter topics, vocabulary grounded against Subtlex-ESP, pre-teach flow before conversation starts. |
| 3 — Content-difficulty index + recommender (weeks 4–6) | The novel subsystem: difficulty-index schema, the "fewest new words" ranking as a real SQL join-and-aggregate query, the book-sequence constraint, the explicit-request override. |
| 4 — Lyrics + reading skills, and evaluation (weeks 6–8) | Both remaining skills, a multi-chapter book with chapter-level difficulty indexing, RAGAS as a baseline check, then the custom evaluation metrics and a written case study. Added 2026-10-06: **listening** in every skill, using text-to-speech only. The tutor speaks its replies after the text appears; clicked words are pronounced; books, stories and poems are narrated sentence by sentence with the current sentence highlighted. The engine is **Piper** (Jason's choice: free, open-source, offline on the CPU, so the project stays cheap and anyone can run it). Voices: an accent setting the learner can toggle. The default is Mexican Spanish (`es_MX`), Jason's focus as an American; Spain (`es_ES`) is the alternative. It applies everywhere audio plays. Built 2026-10-06 (see "Listening" under the Phase 4 plan). A speaking skill was considered first and dropped: pronunciation scoring was its point, and the Claude API takes no audio (it would have needed Azure's pronunciation assessment). |
| 5 — Cloud platform (weeks 8–9) | Migrate the vector store, word bank, and difficulty index onto Databricks (or AWS) while working through the platform course. |
| 6 — Orchestration (weeks 9–10) | Spaced-repetition recalculation and content re-indexing as scheduled dbt models (or Airflow DAGs) — SQL-first, not Python wrapping raw SQL. |
| 7 — Deployment (weeks 10–11) | FastAPI + Docker, a real endpoint, basic observability, and a handful of analytics queries (window functions) over the logs. |
| 8 — Polish & publish (weeks 11–12) | Public repo (copyrighted material gitignored), architecture note, written case study, and only then — honest resume bullets and a new cover-letter evidence block. |
| 9 — Structured learning (after Phase 8; added 2026-10-06) | Jason made the decisions but didn't write the code. Jupyter notebook lessons in which he rebuilds toy versions of each subsystem himself (SQL schema and queries, lemmatization, RAG retrieval, constrained generation, FastAPI, evaluation), with assert-based checks, hints on request and the real code shown only after an attempt, on a sandbox database. Then timed interview drills. Goal: defend the code, not only the design, in a coding interview. |

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

- **Status (as of 2026-10-05):** Phase 0 is complete. Phase 1 has 5 of 7 roadmap steps
  done (the ~20 real conversations are in progress: 2 so far). Phase 2's core
  (pre-teaching) and the new **Phase 2.5 web UI** are done. **Phase 3 is built**
  (schema, indexer, model resolver, both ranking queries, the API and the "what next?"
  screen with a simple reader; see "Content index and recommender" below). Quiroga's
  story collection is in the real database. **Phase 4 is planned** (see "Phase 4 plan"
  below); slices 4.0 (the difficulty ceiling, now 20%), 4.1 (book manifests, older
  accent respellings, candidate measurement), 4.2 (the reading skill) and 4.3 (the lyrics
  skill) are built. The real database (migration 8) holds Quiroga (too hard), *An
  Elementary Spanish Reader* (suggested) and Bécquer's 76 *Rimas* (10 within the
  ceiling). **Listening (Piper text-to-speech) is built** (2026-10-06). Next: slice 4.4
  (evaluation), the last feature before Jason's long stretch of testing and real use.
  - **Built so far:** word bank schema + migrations 001–008; seed (1,086 recognized / 803
    produced after the seed-gap and expression marks; grows with sessions); general lexicon; Tatoeba in
    Chroma (261k sentences); the conversation skill with write-back, topic pre-teaching
    (up to 20 words), "¿cómo se dice?", typed accent markers, accent restoration, wrong-
    word vs wrong-form grading, free lookups, conversation endings with a stored summary,
    conversation-history caching; the web app (FastAPI + React/TypeScript); multi-word
    expressions (2,352 approved, in the corpus from the 2026-10-05 re-analysis); the
    content-difficulty index, the model resolver and the recommender queries (Phase 3).
  - **Sessions:** 1 (2026-10-02, "el jardin", 15 turns) and 2 (2026-10-05, "videogames",
    23 turns, 8 learner messages). Every reply stayed inside the word bank except allowed
    i+1 words (4 in session 1, 1 in session 2); no retries. Each session's review drove
    the fixes listed in the sections below (session 1: pre-teaching, translations,
    markers, accents; session 2: wrong words, free lookups, expressions, *gracias*,
    history caching).
  - Known artifacts kept (the log is append-only): session 1 logged *wáter* (from the
    English in an early "cómo se dice"); session 2 logged *jugo* (a slip for *juego*,
    before wrong-word grading) and 8 grade-1 lookups from testing the click feature.
  - **Notebooks:** 01 (`notebooks/01_data_pipeline.ipynb`) documents every data-processing
    step; 02 (`notebooks/02_conversation.ipynb`) walks through a real conversation on an
    **in-memory copy** of the word bank (~10 API calls, ~10¢ a run; wording varies).
  - **Open items:**
    - **After each real session**, review it in SQL (`turns`, `word_events` by turn,
      `session_stats.sql`); both reviews so far surfaced real fixes.
    - **Seed review of expressions: done 2026-10-06.** The 125 expressions at least as
      frequent as the rank-1,500 word were appended to `seed_candidates.csv`; Jason marked
      26 `p`, 16 `r`, 83 blank (unknown: *tal vez*, *de nuevo*, *por eso*, *acabar de*…),
      and `seed build` (after a backup) loaded them: word bank 1,044 → 1,086 recognized,
      777 → 803 produced, 68 seed events. Effect on the rankings: the reader 19.5% → 19.0%
      unknown, Quiroga 28.4% → 27.6%, *Rimas* within the ceiling 10 → 11.
    - **Context-dependent unresolved words:** *tal* in *¿Qué tal?* once came back "not
      recognized" (an unresolved corrector tie) in a garbled message, and resolved in a
      clean one. Measure how often learner turns hit this before deciding anything.
    - **Resuming a conversation after a server restart** isn't built: the transcript is
      shown read-only. It would mean rebuilding `Tutor.history` from `turns`.
    - **The reader logs no word events:** opening or finishing an item records only
      `content_events`. Teaching an item's new words before reading (and crediting what
      was read) is Phase 4's reading/lyrics skill.
    - ***fue* is always *ser*:** the transformer lemmatizes *fue*/*fueron* as *ser* even
      meaning "went" (all 7,098 Tatoeba tokens), *fui*/*fuimos* always as *ir*. A
      learner's "fue al cine" is credited as *ser*. Predates Phase 3; not yet discussed.
    - **The 1952 respelling isn't in the Tatoeba cache yet** (36 sentences with *fué*/
      *dió*/*fuí*/*vió*). `analysis_setup()` records the tagger and expression list, not
      code, so delete the cache to re-analyze (~90 min) at the next rebuild.
  - Build log, published (private): https://claude.ai/artifact/NAheU5cpSBdxY7QpoeQbHQ.
    Republish it after milestones. The local copy lived in a session's temp folder, so in
    a new session `Artifact read` the URL first, edit that HTML, then republish with
    `url`. The roadmap is `rag-deployment-roadmap.html` in the repo.
  - Public repo: https://github.com/JasonLaRuez/spanish-tutor (remote `origin`; created
    2026-10-01; CLAUDE.md and the roadmap are published as-is, Jason's choice). Re-run
    the history audit before each push: no `data/`, `private/` or `.env`, and no
    notebook outputs.
- **Conversation skill design (decided with Jason 2026-10-01):**
  - **The loop:** retrieve 3–5 fully readable Tatoeba sentences on the topic with
    `vectorstore.search_sentences(query, db.known_vocabulary(conn), max_unknown=0)`, falling
    back to 1. They go into the prompt as on-topic style anchors (the RAG step). Generate
    with Claude, constrained to known words. Detect new words with `lexicon.analyze` +
    `vocabulary()`, teach them, and log events.
  - **Adherence (Jason's choice):** one out-of-bank word per reply is allowed (i+1) and
    taught. Two or more trigger **one** retry that names the offending words; whatever
    remains is then taught. The violation count is logged either way, because it's the
    Phase 4 adherence metric.
  - **Flagged words are always fully taught (Jason's choice).** There's no "I already know
    it" shortcut, even though measurement shows many flags are false: the seed only offered
    ranks 1–1,500, and 60% of the single unknown words in i+1 Tatoeba sentences rank past
    1,500 (*nadar*, *llover*, *idioma*). Expect lessons on some known words; revisit if
    that gets tedious.
  - **Grading (hybrid; placeholder until SM-2):**
    - Rules decide *which* words the learner used: the lexicon, plus the accent fallback
      below. Claude, in the same reply call, flags which used words were misused
      (ser/estar, agreement, tense).
    - `used` correctly = 4, misused = 2. `seen` = NULL (an encounter, not evidence of
      recall). `taught` = NULL.
    - **Wrong word vs wrong form (Jason, 2026-10-05).** Each misuse flag (`Misuse`) says
      whether it's a wrong *form* of the intended word (*luchan*, *videojuegoes*: credited,
      grade 2) or a *wrong word* (*jugo* for *juego*, *preguntar* for *pedir*, *hay* →
      *hace*: never enters production; not taught if unknown; grade 2 only if already
      produced). Checked live on session 2's real mistakes: all classified as intended.
    - **Lookups of known words log nothing (Jason, 2026-10-05):** a reminder isn't a miss.
      Before this, a lookup logged `looked_up` grade 1; session 2 has 8 such events from
      testing the click feature (append-only, they stay). Unknown words are still taught.
    - Measured reason for hybrid: the lemmatizer credits *Soy cansado* as a correct use of
      *ser*, and it can't see grammar errors.
  - **Corrections:** recast plus note. The tutor reuses the correct form naturally in its
    reply, then gives a short separate correction note in English.
  - **Accent fallback for learner input:** *jardin*, *dificil* and *dia* don't match the
    accented lemmas. Strip accents only when the unaccented form isn't itself a lexeme and
    exactly one lexeme matches. Only 129 of 26k lexicon keys collide once accents are
    stripped, almost all function words (*el/él*, *si/sí*, *mas/más*).
  - **`words.ensure_lexeme(lemma, pos)`** (built; used by `LexiconIndex.resolve`) handles
    words not in `lexemes`. If Wiktionary has the word, insert it with its definition.
    Otherwise report it and never log it (*sabo* as a verb). Accent restoration in the
    lemmatizer (below) now catches most unaccented words before this fallback is needed.
  - **Examples at teach time:** re-choose them against the learner's word bank. For rare
    words with no stored example, use the sentence where the word was met.
  - **Model settings:** `claude-opus-5-5` rejects `temperature` and can't disable thinking.
    `langchain-anthropic` 1.7.5 exposes effort as `ChatAnthropic(effort="low")`, which maps
    to `output_config.effort`; use `low` for chat turns. Prompt-cache the system prompt
    and the known-word list. The ~987 lemmas are stable within a session.
- **Conversation additions (requested by Jason 2026-10-02, after session 1):**
  - **Topic pre-teaching:**
    - Before `open()`, `Tutor.pre_teach(n)` teaches up to n topic words. n is 2–20 (raised
      from 10 on 2026-10-05, Jason's request; Enter = 5).
    - Candidates are grounded: the **2,000** Tatoeba sentences nearest the topic give the
      unknown content words seen at least 3 times. They're ranked by topic association,
      count × log(count / expected count from overall frequency).
    - **Depth measured 2026-10-05** (Jason asked for the noise check): 8 topics, top 40
      candidates hand-labeled on-topic vs. noise. Noise in the top 20 was 29% at 300
      sentences, 18% at 1,000, 12% at 2,000; on-topic words offered 49 → 212 → 250. At
      300, most topics had only 5–15 candidates, so even 10 words couldn't be filled.
      Broad topics stay noisy at any depth (*el trabajo*: 16 of 40 on-topic).
    - Claude chooses from `candidate_count(n)` = max(25, 2n) candidates and can't add
      words; invalid picks are dropped (if none are valid, the top scores are used).
    - **Claude may choose fewer than n** rather than pad with off-topic words, giving a
      reason (`TopicWords.fewer_because`). Any shortfall is explained to the learner
      (`TopicChoice.shortfall`: too few candidates, Claude's reason, or both), shown in
      the CLI and the web UI ("12 of 20 words: …").
    - **Practice words fill a gap in new words (Jason, 2026-10-06).** The conversation is
      for production, and reading/songs grow recognition fast, so a topic can run out of
      new words. `topics.topic_pools` returns, from the same 2,000-sentence search, the
      new candidates and the **practice** candidates (recognized, not produced, topic
      score > 0). In the same selection call (`TopicWords.practice`, required field) Claude
      fills any gap up to n from the practice list, never instead of a fitting new word;
      validated like new words (off-list dropped, all-invalid → top scores, capped at the
      gap). The shortfall names both counts.
      - Measured (real bank, 8 topics, read-only): 254 content words recognized but not
        produced; 14–26 practice candidates per topic; those with score ≤ 0 were noise
        (*ya*, *dejar*, *llevar*, *acabar*), so the > 0 filter leaves 6–20.
      - Logged with the opening turn as `seen`, source `pre_teach`, grade NULL (Jason's
        choice: no schema change; an encounter, not a lesson). They join `focus`, not
        `taught` or `known`. First correct use is the ordinary `used` (grade 4).
      - `Lesson.practice` / `LessonOut.practice`: the UI lists them apart ("Words to
        practice") with a "practice" tag. `session_stats.sql` adds `practice` and
        `practice_first_use` (practice ∩ first-ever uses: the gap closing); `pre_taught`
        there and in the transcript = all of today's words (source `pre_teach`), so the
        checklist and its rebuild needed no change.
      - Live (DB copy): with today's bank "el trabajo" got 20 new words, no fill. With all
        but 5 of "la comida"'s new candidates marked read, Claude kept the 5 and filled
        15 on-topic practice words (*delicioso*, *desayuno*, *verdura*…); with none left,
        all 20 were practice. `web/e2e/conversation.mjs` walks it.
    - **The words are for the learner to practice (Jason, 2026-10-05).** The note and the
      system prompt ask the tutor to invite the learner to use them and to use *some*
      itself where natural, not all. The web UI's "Today's words" checklist ticks each one
      off when the learner uses it (`TutorTurn.used`; after a reload, rebuilt from the
      transcript's `pre_taught` and `used`).
    - The words join the frozen prompt vocabulary and are named in every turn's note.
      Their events are logged as `taught`, `source='pre_teach'`, on the opening turn.
  - **Ending a conversation (Jason, 2026-10-05):**
    - A message *ending* with a goodbye (*hasta luego / mañana / pronto / la próxima*,
      *adiós*, *chao/chau*, *nos vemos*; accents optional, an optional short address like
      ", profesor") ends it: `conversation.is_farewell`, checked in `reply_to` after markers
      are expanded. It's a normal turn (its words are credited), with `FAREWELL_NOTE` asking
      for a short goodbye and no question.
    - The web UI's **¡Hasta luego!** button (`POST /api/sessions/{id}/end`) and the CLI's
      `/salir` end it with `Tutor.end()` and no learner text: the tutor says goodbye
      (`GOODBYE_MESSAGE`) and no words are credited, since the learner wrote none.
    - Then: `sessions.ended_at` is set, the stats are computed, and the tutor's notes are
      written by one Claude call with its own schema (`SessionNotes`: went well, 2–3 things
      to work on), from the transcript, its correction notes and the stats. If that call
      fails, the conversation still ends with its stats (`Ending.notes_error`).
    - **Schema (migration 004, Jason approved the SQL as proposed):**
      `sessions.ended_at` (NULL = open or abandoned) and `session_summaries` (one row per
      session, PK = FK to sessions; the notes, the model, and the same cost columns as
      `turns`). "Work on" points are one TEXT column, one per line (2–3 lines always shown
      together; a table would be over-normalized).
    - **Stats are never stored:** `sql/queries/session_stats.sql` computes them from the
      log. "Used for the first time" is a `ROW_NUMBER()` over each word's `used` events;
      it needs a `LEFT JOIN` to `turns`, or seed events (no turn) drop out and every seed
      `p` word counts as new. Cross-checked against the word bank: per-session first uses
      equal the production entries learned via conversation (15 + 15 for sessions 1–2).
    - Ended conversations are closed on the server; History marks them and shows the
      summary card under the transcript.
  - **Translation turns:**
    - The CLI detects `¿Cómo se dice "…"?` on the raw input, which is lenient about
      accents, quotes and "en español".
    - `Tutor.translate` pauses the conversation for one turn. Every new word in the
      answer is taught. The question gets no `used` events, because it's English.
    - Both turns are stored with `kind='translation'`, and the adherence and
      completeness SQL counts only `kind='conversation'` and `source='conversation'`.
  - **Typed markers** (`keyboard.expand_markers`, Jason's spec): `'a` → á, `~n` → ñ, `:u`
    → ü, and `?`/`!` directly before a word → ¿/¡. The CLI echoes the converted text, and
    the quoted English in a "cómo se dice" request is never converted.
  - **Cost-saving decisions on the prompt cache (2026-10-02, measured):**
    - **Lifetime: 1 hour.**
      - The API offers 5 minutes (the default) or 1 hour; there's no "until the
        conversation ends". Every read resets the timer for free, so the cache lasts the
        whole conversation as long as no gap exceeds the lifetime.
      - The only extra cost is the write: 2× instead of 1.25× base input for the ~5k
        prefix, so 4¢ instead of 2.5¢ once per session. Reads are 0.05× either way
        (~0.1¢ a turn).
      - In session 1, the 5-minute cache would have survived every gap except a
        33-minute break, which re-sent the prefix at full price. 1 hour is about 1.5¢ of
        insurance per session against that.
    - **One output schema for all conversation requests.**
      - The structured-output schema is part of the cached prompt, so each schema gets its
        own cache entry. Measured: with the same prefix, a `TutorReply` and a separate
        `Translation` schema each wrote ~5k tokens.
      - Translations therefore use `TutorReply` too (Spanish in `reply_es`, explanation in
        `note_en`, as the turn's note instructs).
      - A "cómo se dice" turn went from ~5,470 uncached tokens to ~650 uncached + 5,290
        cached (~2.7¢ → ~0.4¢). Jason expects to use it often.
      - The topic-word selection keeps its own schema: it's one call per session, with a
        different prompt and nothing to share. So do the end-of-conversation notes.
    - **The conversation history is cached too (2026-10-05, Jason: "minimizing the
      monetary cost of using the tool is an important objective").**
      - Before, only the system prompt + vocabulary (~5.4k tokens) was cached; the history
        was re-sent at full price each turn (453 → 2,695 uncached tokens per turn in
        session 2), roughly 85% of a session's input cost.
      - Now a second breakpoint (5-minute TTL) sits on the latest learner message
        (`conversation.learner_message(text, cached=True)`); the system keeps its 1-hour
        entry (longer TTL first, as the API requires). History messages are stored as the
        same text block without the mark, so the prefix bytes match.
      - Replayed on sessions 1–2 with their real gaps (Opus 5.5: $4/$20 per MTok, reads
        0.05×, writes 1.25× 5-min / 2× 1-hour): −23% and −28% per session; the 5-minute
        TTL beat 1 hour (−18%, −24%) because gaps are almost always under 5 minutes.
      - Verified live on a database copy: reads grow every turn (5,927 → 6,396), writes
        are only the last exchange (29–185 tokens), full-price input is just the per-turn
        note (~80–160); 0.41–0.65¢ per turn, versus ~1–1.6¢ before.
      - **Migration 005 (Jason approved the SQL):** `turns.cache_write_5m_tokens` and
        `turns.cache_write_1h_tokens`, the part of `input_tokens` written to the cache by
        lifetime (NULL before migration 5). Exact per-turn cost =
        (input − w5m − w1h)·1 + w5m·1.25 + w1h·2 + reads·0.05 at $4/MTok, + output at
        $20/MTok.
      - What's left: output (thinking included) is ~6–7.5¢ of a session after caching;
        lowering it means effort/quality tradeoffs, not yet discussed.
  - **Tagger limit seen in testing (fixed 2026-10-02, see "Tagger"):** *riego* in "y riego
    las plantas" (I water) was tagged as the noun *riego* (irrigation) and taught as a new
    word, although *regar* had been pre-taught.
- **Known data limits:** sentence-initial *Sé* is tagged as the imperative of *ser* both
  in *Sé amable* (right) and *Sé que…* (wrong, it's *saber*), about half each across 537
  tokens; sentence-initial words are sometimes tagged as names and dropped; ~100
  NOUN-tagged *conmigo* tokens became an ADV entry; an expression isn't matched with a
  word inserted into it (*echar mucho de menos*).
  - **Lowercase words tagged PROPN: fixed in the pipeline on 2026-10-01.**
    - The problem: spaCy tags some common words as proper nouns even lowercase and
      mid-sentence (*Me gusta la nube.*). PROPN was dropped, so 8.5k Tatoeba tokens
      (1,652 distinct dictionary words: *tu*, *perro*, *llover*, *madre*…) were
      invisible. That included the conversation skill's adherence check.
    - The fix: `LemmaCorrector.retag_proper_noun`, called from `lexicon.normalize` for
      PROPN tokens written lowercase.
    - Rule, in order:
      1. Personal pronoun forms become PRON (*contigo*).
      2. If the headword has a function-word POS, only those POS count (Wiktionary's
         noun *mi* is the musical note E).
      3. A headword under one remaining POS gets it.
      4. If it has several POS including NOUN, use NOUN.
      5. Otherwise treat it as an inflected form and use the form-of lemma.
    - Rules 1 and 2 were added after measuring the first version against the word bank:
      2.7% of re-tagged tokens landed on a POS Jason didn't have; now 0.8%. The rest is
      mostly ADJ/NOUN words (*loco*, *azul*), where NOUN is a defensible guess.
    - The first version left 3 orphan lexemes (*mi*, *yo*, *cualquiera* as NOUN). They
      were deleted with Jason's approval, since no events referenced them.
    - Ties are reported in `lemma_corrections.csv`, never guessed. Capitalized PROPN
      stays a name. Only PROPN was affected; measured, no other tag was.
    - Measured outcome on the dropped tokens: ~87% recovered, 8.9% correctly dropped as
      non-words (*toki pona*, English), and 4.4% left as unguessed ties (*tuyo*,
      *rosas*).
    - It runs inside every analysis, so a fresh build needs no extra step. Existing
      data needs a re-run of `ingest.tatoeba` (move the old jsonl aside first),
      `build_lexicon` and `index_tatoeba --update-vocab`; Jason's data was rebuilt
      2026-10-01.
  - A single typed word is a separate case: a lone *perro* is always tagged PROPN, with
    no context. `/q` avoids it with `LexiconIndex.headword`.
  - **Wiktionary misspelling entries: fixed in the pipeline on 2026-10-01.**
    - The problem: Wiktionary lists common typos as entries ("*dia*: misspelling of
      *día*"). Tatoeba contains them, so 35 had become lexicon words (*reir*, *rio*,
      *tambien*, *mas* ADV…). For learner typing, an exact match to one beat the accent
      fallback.
    - The fix: `Wiktionary.misspellings()` covers entries whose every sense points to
      the same word, with at least one tagged "misspelling". `LemmaCorrector` respells
      them and then corrects the result in turn (*habia* → *había* → *haber*).
    - It also catches learner typos: *aser* → *hacer*, *haci* → *así*.
    - "Alternative form" entries are deliberately not redirected: *buen* → *bueno* and
      *mi* → *mío* are separate words. "Obsolete spelling" is not redirected either,
      because it has odd cases (*ay* → *hay*).
    - Approved cleanup: the orphaned misspelling rows and the 3 rows from the first
      re-tag rule were deleted (no events referenced them; database backed up first).
    - *mas* ADV is kept: it has seed events, and the log is append-only. It's a
      harmless duplicate, since Jason already knows *más* ADV in both modes. Its
      frequency was cleared to NULL by the 2026-10-02 rebuild, because the build no
      longer produces it.
  - `find_example` prefers 4–10-word sentences, the lexicon picker's range. It first
    returned fragments (*¡Disparad!*, *¿Subes?*).
  - **Missing accents: restored in the text, before tagging (2026-10-02).**
    - The problem, from session 1: spaCy lemmatized *detras* as an invented *detra*, and
      ñ-folding was deliberately absent, so *manana* couldn't match.
    - `lexicon.AccentRestorer` runs in `analyze()` whenever a corrector is given.
    - It replaces a word only when all of these hold:
      1. it was typed with no marks;
      2. it isn't a word as typed (spellings that are only misspellings or obsolete
         entries, from `Wiktionary.nonstandard_spellings()`, don't count as words);
      3. exactly one candidate matches when accents and ñ are folded, or one is clearly
         most frequent;
      4. the candidate occurs at least 20 times in SUBTLEX;
      5. if capitalized, it starts a sentence.
    - Rules 1 and 5 came from the first rebuild's review file (*sudán* → *sudan*,
      *Gales* → *Galés*).
    - Tatoeba: 579 tokens restored (`data/processed/accent_restorations.csv`).
    - Accepted limits: a spelling that is also a word is never restored (*ano* for
      *año*), and a name at a sentence start may be (*Maria* → *María*).
- **Notebook:** `notebooks/01_data_pipeline.ipynb` is now the source; edit it directly. The
  generator script used to create it was temporary and no longer exists.
  - Jason's rule: every change to data processing (lemmatization, correction, lexicon,
    indexing, lookups) is documented in notebook 01 as part of the change. The
    conversation skill is in `notebooks/02_conversation.ipynb`; verify edits to it the same
    way (it makes real API calls, so expect a few cents and varying wording).
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
  The web API is `src/spanish_tutor/api/`, the web UI `web/` (its own `README.md`), and the
  progress/history SQL `sql/queries/` (loaded by `progress.py`).
- **Web UI (roadmap Phase 2.5, slice U1 built 2026-10-02; Jason's choices):**
  - **Phones (built 2026-10-06, Jason's request, before slice 4.4d):** below 768 px
    (`md`) the sidebar is a drawer behind a ☰ top bar. Its open state is derived from
    the page it was opened on, so navigating closes it without an effect (oxlint's
    set-state-in-effect rule); it also closes on Escape or a tap on the backdrop, and is
    `invisible` while closed (out of the tab order). Measured first at 390 × 844: the
    fixed sidebar left the content 134 px wide. Fixes then found by screenshots: the
    chat header wraps, a grid list on Progress needed `grid-cols-1` (a nowrap item grew
    its column 780 px wide), secondary table columns (author, kind, source) are hidden
    below `sm`. iPhone Safari plays audio only from an element first played during a
    tap, so the shared player plays a silent WAV on the first tap or key
    (`state/speech.tsx`); not yet confirmed on a real iPhone. `web/e2e/phone.mjs` checks
    every page (no model calls). Phone access is `serve --host 0.0.0.0` on a trusted
    network (no login; the README says so).
  - Stack: a FastAPI JSON API plus React + TypeScript (Vite, Tailwind v4, React Router).
    Built now, before Phase 3, so the remaining real conversations happen in it. Each
    later phase adds its screen: U2 recommend (Phase 3), U3 songs/books and U4
    level-readiness rings (Phase 4). Phase 7 only containerizes and deploys it.
  - `spanish-tutor serve` loads the slow resources once (`conversation.load_resources`)
    and serves `web/dist` when it's built. One learner, one process: open conversations
    live in memory; a restart closes them (their transcripts stay viewable).
  - Threads: conversations share one connection (`check_same_thread=False`) and the
    lexicon index, so every use holds one lock. Read endpoints open their own connection
    per request, so progress and history never wait for a ~6 s reply.
  - Routing of learner messages ("¿cómo se dice?" on the raw text, else markers expanded)
    is `conversation.reply_to`, shared with the CLI's behavior.
  - The UI's types are generated from the API's OpenAPI schema (`npm run gen:api`;
    `openapi-typescript` runs via `npx` because it doesn't accept TypeScript 6 yet).
    Regenerate after any API change.
  - Progress SQL decisions (Jason): keep the frequency bands (100 / 500 / 1k / 2k / 5k);
    growth per session, not per day; "try using these" ordered by frequency; history
    shows "¿cómo se dice?" turns and corrections (tutor replies with a note).
  - Charts follow the dataviz palette check: blue = recognize, orange = can produce, both
    validated in light and dark; each chart has a table view.
  - Verified end to end in headless Edge (`playwright-core`; the current walkthroughs are
    `web/e2e/reading.mjs` and `lyrics.mjs`, see its README) against a **copy**
    of the word bank (`TUTOR_DB_PATH`), which found and fixed two cursor races (accent
    keyboard, "¿cómo se dice?" template). Never test the UI against the real database.
- **All text → `(lemma, pos)` goes through `src/spanish_tutor/lexicon.py`** (spaCy
  `es_dep_news_trf`: NFC, lowercase, accents kept, AUX→VERB, `del`/`al` expanded, clitic
  verbs reduced to the verb, and **personal pronouns keep their own form**, because spaCy
  would otherwise merge *me/nos/conmigo* into *yo* and *se/lo/le* into *él*). Phase 3
  content indexing must reuse it, or the word bank and difficulty index disagree about
  what's "new".
- **Tagger: `es_dep_news_trf` (switched from `es_core_news_md` 2026-10-02, Jason's choice;
  also a portfolio point: a measured model upgrade).**
  - Why: on hand-labeled Tatoeba samples of verb forms md had tagged NOUN (*Yo trabajo*,
    *No toques*, *Cuando compras algo*), md found 0/51 verbs, `es_core_news_lg` 11, the
    transformer 50, with 0 of 52 real nouns made verbs. Learner-style sentences: 3, 4, 6
    of 8.
  - Whole corpus: 2.0% of 3.2M tokens changed. Hand check of 50 random changes: new right
    39, old right 5, 5 ties, 1 both wrong. 14.6k dropped tokens recovered (mostly
    sentence-initial verbs); lowercase-PROPN re-tags 10,168 → 1,536; lemma corrections
    95k → 54k; *ven* now *venir* (was *ver*).
  - Regression found and fixed: the transformer lemmatizes *vos* as *vo* (a Wiktionary
    word), so personal pronouns now keep their own form by the *form*, not only by the
    tagger's lemma.
  - ***gracias* (fixed 2026-10-05):** the transformer files the thanks under *gracia*
    ("grace") in 451 of 1,127 Tatoeba tokens, so tutor replies taught *gracia*.
    `lexicon.LEMMA_FIXES` maps (form *gracias*, lemma *gracia*) to *gracias*, keeping the
    tagger's POS, onto entries Jason knows (INTJ and NOUN). A single-word rule, Jason's
    call: the plural of *gracia* in that sense is vanishingly rare. Add to this table only
    for measured, frequent errors; general rules come first.
  - Cost: ~10 ms/sentence on CPU (batch size 64 is fastest), a 6 s model load, a full
    Tatoeba analysis takes ~90 min (was 15).
  - The cached corpus records its tagger and the expression list's fingerprint in
    `tatoeba_analyzed.meta.json`; `build_lexicon` and `index_tatoeba` refuse a cache made
    with a different setup, and `ingest.tatoeba` re-analyzes it. It doesn't record code
    changes: delete the cache to re-analyze after changing `lexicon.py`.
  - Rebuild results (2026-10-02): lexicon 663 added, 741 removed (unreferenced junk such
    as *empecer*, the noun *miente*), 0 frequencies cleared; 31k Chroma vocab entries
    updated; word bank unchanged (999 recognized / 745 produced).
  - **Seed gap:** 78 words entered the top 1,500 that Jason never reviewed (*hola*,
    *callar*, *nadar*, *vamos*, *perdón*; 9 are known words under a new POS, e.g.
    *francés* NOUN). They're appended to `seed_candidates.csv` (new ranks). Jason marked
    them 2026-10-03 (14 `r`, 17 `p`, 47 left unknown) and `seed build` loaded them: word
    bank 1,030 recognized / 762 produced.
- **Multi-word expressions (`ingest/expressions.py`, added 2026-10-05; Jason's decisions):**
  - Why: *sin embargo* taught *embargo* ("seizure") in session 2; fixed expressions must be
    one vocabulary item.
  - Source: Wiktionary's multi-word entries (15,602 without names and proverbs). Naive
    matching is harmful (measured): the top matches were function-word pairs (*de la*,
    *a la*, *lo que*), phrasebook sentences (*no sé*, *dónde estás*), and wrong senses
    (*la vida* is listed only as slang for prostitution).
  - Pipeline (`python -m spanish_tutor.ingest.expressions candidates|review|collect`):
    1. Rule filters: drop phrase/proverb/article/name entries and senses that are literal
       ("used other than figuratively"), vulgar, derogatory, archaic, obsolete, dated,
       historical or alternative spellings; keep lemma sequences found in ≥3 Tatoeba
       sentences: 3,066 candidates (`expression_candidates.jsonl`).
    2. Review: Claude Opus 5.5 via the Batch API (Jason's choice of model) sees each
       candidate's senses and up to 4 real Tatoeba sentences and decides keep + which
       sense (`expression_reviews.jsonl`, with the reviewer). A pilot on 8 cases was
       right on all 8 (kept *sin embargo*, *por favor*, *un poco*, *darse cuenta*;
       rejected *a la*, *lo que*, *de una*, *la vida*). Estimated $1.50, re-estimated
       $7.30 after the pilot (examples make prompts ~805 tokens); Jason approved;
       actual **$6.68** (2.23M in, 221k out).
    3. Kept **2,352** (`expressions.jsonl`: phrase, lemma sequence, the reviewed sense as
       definition, sentence count). The review kept 2,371. The other 19 had no listed
       sense that fits the corpus (sense 0), so they're dropped rather than taught with a
       wrong definition. Examples: *a punto* is listed only as "at the ready";
       *con gusto*, *dar lugar*. Their main uses survive as longer approved expressions
       (*a punto de*, *dejar de lado*, *con tal de que*). Could be revisited by having
       Claude write those 19 definitions (marked as model-written); not discussed with
       Jason yet. Top: *por qué*, *por favor*, *un poco*, *después de*,
       *ya no*, *a menudo*, *a veces*, *así que*, *darse cuenta*, *de acuerdo*.
  - Matching (`lexicon.ExpressionMatcher`, applied in `analyze` when the corrector has
    it): lemma sequences over consecutive whole tokens, longest first; inflection is free
    (*me di cuenta* → *darse cuenta*), a contraction counts with both lemmas (*al menos*);
    no match across a name or with a word inserted (*echar mucho de menos*), accepted.
  - **One unit (Jason's choice):** the first token gets (phrase, `EXPR`) and the whole
    phrase as its surface; the other tokens keep their surface with no analyses. So the
    phrase is credited/taught, never its parts there.
  - Lexicon: `build_entries(..., expressions=...)` adds approved phrases found in the
    corpus with the reviewed definition; frequency is Tatoeba occurrences per million
    Tatoeba tokens (SUBTLEX has no phrases), only roughly comparable to the words'.
  - The data files are derived (CC BY-SA) and gitignored like the rest of
    `data/processed/`.
  - **Rebuild results (2026-10-05):**
    - Re-analysis vs the previous cache: 88,261 EXPR tokens (2,310 distinct phrases; top:
      *por qué* 5,575, *por favor* 3,584, *un poco* 2,371). The only other token change:
      341 *gracias* moved from *gracia*.
    - Lexicon: 2,309 expressions added. 113 rows removed, almost all words that only occur
      inside an expression (*repente*, *bordo*, *vano*, *obstante*, *antemano*,
      *santiamén*), which are now taught as the expression.
    - Word bank unchanged (1,044 recognized / 777 produced; 2,417 events). The real DB is
      at migration 5. The foreign-key check is clean.
    - Bug found in that run and fixed: adding `load_expression_matcher` had displaced
      `load_corrector`'s `@cache`. So the run's reports came from a fresh, empty
      corrector (0 corrections), and every `analyze()` call rebuilt the corrector.
      `test_shared_loaders_are_cached` guards it. The corrections CSV was restored from
      the previous run (the analyses are identical), and the accent restorations were
      regenerated from the text.
- **Phase 4 plan (approved by Jason 2026-10-05).** Slices, in order; each starts with a
  short detailed plan, and new SQL is proposed for approval:
  - **4.0 Ceiling (built):** an item with more than `recommend.MAX_UNKNOWN_SHARE` = 20% of
    its running words unknown (books: whole-book density, started or not) is never
    suggested or surprised; it's listed under "Too hard for now" with its % known, and can
    still be opened. The queries take `:max_unknown` and `:too_hard` (one ranking, either
    side). Jason first chose 10% (every new word is pre-taught; reading research puts
    assisted reading at 95% known), then **raised it to 20% on 2026-10-06** after slice
    4.1's measurement showed no public-domain text could pass 10% (below). Quiroga is 28%
    unknown, so it's too hard now.
  - **4.1 Easier public-domain texts (built 2026-10-06):**
    - **Manifests:** `ingest/gutenberg.py` splits by a hand-written manifest per book
      (`ingest/books/<ebook>.json`, committed; the text never): `chapters` = [heading as in
      the text, title], `end` (line where reading text stops), `remove` (regexes: margin
      numbers, `[Note …]`, illustrations, footnote markers), `replace` (markup, `--`).
      Headings match whole lines in order, or the split fails loudly. 13507's manifest
      reproduces the earlier chapter files byte for byte.
    - **Measured nine candidates** (read-only, against the word bank): no real text came
      near 10% unknown. Best: *An Elementary Spanish Reader* (Harrison 1912, ebook
      22065) 19.7% rough / 19.4% properly split; *Libro segundo de lectura* 23.2%; the
      rest 26–33%. Even with every unreviewed word to frequency rank 5,000 in the bank, the
      best would be 12%. So Jason raised the ceiling to 20%. About 6% of these books'
      running words are frequent words (rank ≤ 1,500) not in the bank: a third are the 125
      unmarked expressions, the rest words left unmarked in the seed review (*aquel*,
      *notar*, *hallar*, *coger*). Jason declined a seed review of ranks 1,501+ for now.
    - **Older accent spellings (Jason approved):** Wiktionary's 38 "obsolete spellings"
      that differ from the modern word only in accents (*á*, *é*, *ú*, *ántes*, *órden*,
      *fuéron*, *segun*) are respelled before tagging along with the 1952 ones
      (`Wiktionary.obsolete_accent_spellings`; 49 respellings in all). *ó* (listed as a
      2010 numerals rule) is not among them. Worth 2.4 points of a 1909 reader's unknown
      share. Not yet in the Tatoeba cache or the real Quiroga index (re-index is free).
    - The plain-text 15353 (*A First Spanish Reader*) lost all accents (`?` for `¿`); its
      HTML edition has them. Manifests read the `.txt` edition only, for now.
  - **4.2 Reading skill:** pre-teach **every** new word in batches of ~20 in order of
    **first appearance** (Jason), logged `taught` (source `reading`) as each batch is
    studied; read anytime with click-to-lookup (unknown taught, known free); finishing logs
    `seen` for the known words; then discuss the text with the tutor (generalized `Tutor`
    with skill/source/document; RAG over the text's passages, embedded in memory). Text
    analyzed once per session (first appearances, contexts). Migration 7 widens
    `turns.kind` (`study`, `reading`, `attempt`) by table rebuild so every event keeps its
    turn. Books library page.
    - **Built 2026-10-06.** `reading.ReadingSession` (one per started item, in memory like
      tutors; skill `reading`, `content_events.session_id` set): `next_batch` (logs
      nothing), `study` (one `study` turn, role tutor, `text_es` = the words, `taught`
      events), `look_up` (unknown → a one-word study turn; known → free), `finish` (a
      `reading` turn, role learner, with `seen` for every known word; ends the session;
      finishes the item), `discuss` (a `Tutor` continuing the session: `skill`/`source`
      'reading', `first_turn_no`, its own opening, `prompts/reading.md` appended to the
      instructions, `TextPassages` = the text in 3-sentence passages in an
      `InMemoryVectorStore` with the jina embeddings; the top 3 go in each turn's note).
      Words resolve as the indexer resolved them (lexicon, dictionary, `word_resolutions`).
    - The talk is offered after Finished, not automatic (Jason). It's registered as an
      open conversation, so the chat endpoints and page serve it unchanged.
    - **Migration 7 (approved):** `turns` rebuilt (create, copy, drop, rename) with the
      wider CHECK. `db.init_schema` runs a migration marked `-- foreign_keys: off` with
      foreign keys off and checks `PRAGMA foreign_key_check` before committing (rolls back
      on a broken reference). Verified on a copy: 54 turns and 2,417 events kept, word
      bank unchanged. The real DB migrates at the next startup.
    - `content.paragraphs` gives the text as paragraphs (a song's stanzas) of sentences
      (lines); `content.sentences` flattens it (identical units on all 39 real chapters).
    - API: `POST /api/reading` (start; analyzes the text, a few seconds for a long
      chapter), `GET /api/reading/{id}`, `GET …/batch?n=`, `POST …/study`,
      `POST …/lookup`, `POST …/finish`, `POST …/discuss` (409 before finishing). Web: the
      reading page (`/reading/:sessionId`: Study / Read tabs, progress bar, unstudied
      words underlined, a "studied up to here" marker), the Books page, and "What next?"
      starting reading sessions. The plain Phase 3 reader is gone (its endpoints stay).
    - Checked in headless Edge on a copy: chapter 1 of the reader had 14 new words in one
      batch; studying them moved the book from 19% to 18% unknown; finishing moved it to
      chapter 2. The talk itself was not exercised in the browser (it calls the model);
      the API tests and notebook 02 cover it.
    - Known gaps: the sidebar's word counts refresh on page changes only; inflected forms
      of old spellings (*órdenes*) aren't respelled (4 tokens in the corpus).
  - **4.3 Lyrics skill:** study batches, then **try first** (Jason translates chosen lines
    Spanish → English; comprehension, not graded), then compare natural and literal
    translations side by side with figurative notes and a comment on his attempt;
    expressions grounded in their reviewed sense plus a Tatoeba example. One structured
    call per song, stored in `song_translations` (local DB). Bécquer's *Rimas* for the
    public demo; Jason's songs in `private/lyrics/`. Songs library page.
    - **Built 2026-10-06 (Jason's choices: runs + lines storage, a new `poem` kind, the
      talk offered after songs too, medium effort for the translation).**
      `lyrics.LyricsSession(ReadingSession)` (skill and source `lyrics`; a reading
      session's `skill` is now a class attribute): `expressions()` (EXPR lexemes per line,
      with the reviewed sense and the lexicon's Tatoeba example), `translation()` (the
      latest stored run if it covers exactly the song's lines, else one structured call:
      `SongTranslation`, every line exactly once, one retry naming the problem, then
      `TranslationError`), `attempt({line: text})` (one `AttemptFeedback` call via
      `generate.ask`; a learner `attempt` turn with the Spanish lines and the English in
      `note_en`, a tutor `attempt` turn with the comments and the call's cost; no `used`
      events). `Resources.translate` = `ClaudeGenerator(effort="medium").ask`.
    - **Migration 8 (approved):** `content_items` rebuilt with kind `poem` (foreign keys
      off, checked before commit); `song_translations` (a run: model, tokens, latency;
      append-only, the latest shown) and `song_translation_lines` (run, line_no,
      natural_en, literal_en, note_en; WITHOUT ROWID). Songs and poems are `content.VERSE`
      (analyzed by line, in stanzas; ranked with stories).
    - **The Rimas:** manifest 53552 (`kind: "poems"`, `start: "RIMAS"`, because earlier
      sections number their parts the same way; ends at `FIN`); `content add-poems`
      adds each file as a poem. Measured: 10 of 76 within 20% (Rima XXIII: 22 words, 2 new;
      median 27.5% unknown). Resolver on the real DB: $0.125 (estimate $0.13).
    - Live: Rima XXIII translated for ~1¢ (973 in / 327 out) and compared for ~0.5¢, both
      well judged ("cielo" as heaven vs. sky, the implied "I'd give"). A 12-line rima
      should cost ~2–3¢ to translate. API: `GET /api/reading/{id}/translation` (makes it
      the first time; 409 for a non-song, 502 on a bad translation),
      `POST …/attempt`; `ReadingState.skill`. Web: Try first / Compare steps on the
      reading page for songs and poems, a Songs & poems library page; every Phase 4
      placeholder in the nav is gone.
  - **4.4 Evaluation:** adherence, teaching completeness (first make lookup events
    distinguishable: they currently log `taught` on the last tutor turn, showing as false
    gaps), translation naturalness (LLM-as-judge with **Sonnet 5.5**, Jason's choice;
    validated against his hand ratings; **first measure the judge's consistency**: in
    slice 4.3 the attempt comparison rated the same attempt "close" in one run and
    "right" in another, so run each judgment several times and report agreement before
    any judge-based number), recommendation quality (predicted vs. taught,
    default take-rate, abandonment around the ceiling); RAGAS baseline on the expression
    and Tatoeba retrievals (check install compatibility first; else implement the two
    metrics directly). Notebook 03, Progress-page panel, written case study.
    - **Plan (approved 2026-10-06).** The real log is thin (2 conversations, 25 tutor
      replies, no reading or lyrics use yet), so every metric is SQL that runs both on the
      real log (it fills during Jason's testing stretch) and on a **benchmark**: scripted
      learner messages with planted errors, run with the real model on a database copy.
      Slices: 4.4a instrumentation, 4.4b log metrics, 4.4c conversation benchmark
      (adherence, completeness, grading accuracy on planted errors, new-word detector
      precision from a hand-checked sample), 4.4d translation naturalness (judge
      consistency first, then calibration against Jason, then scores), 4.4e retrieval
      (context relevance, faithfulness), 4.4f notebook 03, Progress panel, write-up.
      Budget about $2-3 for one full pass (Jason approved; pilot first, re-approve if the
      estimate moves).
    - **Jason's decisions:** no table of known-word lookups (they stay unlogged); hand
      ratings on a **rating page in the app** (not a CSV); Sonnet 5.5 as judge.
    - **RAGAS can't be installed:** ragas 0.4.3's dependencies need `huggingface-hub` ≥
      1.0, but the pinned `transformers` 4.53.2 requires < 1.0 (checked 2026-10-06; it
      would also pull in openai, langchain-openai, sqlalchemy, pyarrow). Its two metrics
      are implemented directly instead, as the plan allowed.
    - **4.4a (built):** a conversation lookup of an unknown word is taught on its own
      one-word `study` turn (as in the reader), not on the last tutor reply. No real
      session had one, so the measured gap count on the real log was 0 either way.
      **Migration 9** (approved): `eval_runs`, `eval_items` (what's rated: a JSON
      snapshot plus a text `source_ref`, no foreign key, since items come from several
      tables and benchmark copies), `ratings` (append-only; judges and Jason in one table,
      `rater = 'human'` iff no run; judge repeats are rows).
    - **4.4b (built):** `sql/queries/eval_adherence.sql`, `eval_completeness.sql`,
      `eval_completeness_gaps.sql`, `eval_reading.sql` (predicted new words vs taught,
      studied before finishing; "known at the start" rebuilt from the log),
      `eval_recommendation.sql` (take rate; finished by difficulty band at the time of
      the start). `evaluation/metrics.py` turns counts into rates with n and a 95% Wilson
      interval; `python -m spanish_tutor.evaluation report [--db] [--session]` (read-only).
      Real log, 2026-10-06: adherence 25/25 (95% CI 87-100%), completeness 5/5 (57-100%),
      reading and recommendation n = 0.
    - **4.4c (built):** `evaluation/benchmark.json` (8 topics × 5 messages: 24 planted
      mistakes, 14 wrong form / 10 wrong word, 16 correct; Jason reviewed it),
      `evaluation/benchmark.py` (a frozen snapshot `data/processed/eval/benchmark-base.db`,
      each run on a copy; a `Recorder` keeps the misuse flags, which the database doesn't
      store, and every call's cost; metrics take `:from_session` because the snapshot
      also holds the real history, a bug the pilot found). The Rate page
      (`evaluation/ratings.py`, `/api/eval*`, `pages/Rate.tsx`) and its queue.
      - **Results (2026-10-06, 80.1¢ for 8 topics):** 24/24 planted mistakes flagged, all
        as the right kind; 16/16 correct messages unflagged; 48/48 replies within the
        limit, no retries; completeness 8/8. The textbook mistakes all pass, so the set
        is too easy to find limits: a harder v2 (subjunctive, por/para, two mistakes in a
        message) is a later option; for now it's a regression test.
      - **New-word detector precision (Jason rated 13 taught words in chat, recorded as
        `human`): 8/13 = 62% (95% CI 36-82%)**; real log 2/5, benchmark 6/8. No tagging
        errors. My guesses were wrong: *qué* INTJ ("¡Qué bien!"), *como* ADP and *en casa*
        were really new to him. The 5 known ones are a word-bank coverage gap, not a
        detector error: 4 rank 1,821-2,656 (past the seed review's 1,500; never offered),
        and *cada* (rank 231) was offered but left blank. The gap closes itself (each
        taught word joins the bank); a seed review of ranks 1,501-3,000 would close it
        faster, if extra lessons on known words become annoying (Jason's call).
    - **4.4d (built 2026-10-07):** `evaluation/translation.py` (on a database copy:
      a lyrics session logs the poem as started, which would skew the real log's
      recommendation metrics), `evaluation/agreement.py` (Krippendorff's alpha; matches
      the paper's example: 0.743 nominal / 0.815 ordinal / 0.849 interval),
      `sql/queries/eval_judge_consistency.sql`. Judge: Sonnet 5.5, medium effort, one
      call per poem, 5 repeats, scores naturalness and faithfulness 1-5 (`RUBRIC`);
      invalid judgments retried once, then skipped. Attempts: 18 (6 lines × right / close /
      missed, Jason reviewed them) through the app's own comparison, 5 repeats.
      - **Rubric v2 (Jason):** "supplying a word the Spanish implies is faithful, not an
        addition" (English needs words Spanish leaves unsaid). In the v1 pilot the judge
        wobbled 4/5 on "For one glance, **I'd give** a world" (5,4,4,5,5); under v2: 5×5.
      - **Full run (83.3¢; judge run 3, attempt run 4 in the real DB):** 88 lines × 5.
        Naturalness alpha 0.851 (all 5 agree 58/88, within 1 point 88/88, mean 4.22);
        faithfulness alpha 0.824 (64/88, 86/88, mean 4.44): both above 0.80, the level
        Krippendorff calls reliable. The 2 faithfulness lines that spread >1 point and
        the lowest naturalness lines ("of your sighs is;") are sentences split across
        lines: line-by-line translation (for side-by-side display) makes fragments. Some
        faithfulness reasons cite awkwardness (criterion bleed). Attempt verdicts: alpha
        1.0, 85/90 match the intended verdict; the 5 misses are one attempt, "Do you know
        when it comes back?" for *¿Sabes tú adónde va?*, called "close" 5/5 although its
        own comment says the meaning is wrong: the app's comparison is lenient when the
        form is right. A perfectly consistent judge can be consistently wrong.
      - Spent on 4.4 so far: about $1.77 of the approved $2-3.
      - **Next:** calibration (Jason rates naturalness of a sample of the 88 queued lines;
        judge vs Jason), then 4.4e retrieval (~50¢), 4.4f.
  - **4.5 Readiness rings:** only after researching the PCIC license with Jason.
  - **Listening (built 2026-10-06; Jason's choices).** Text-to-speech in every skill, as
    the last feature before 4.4. A speaking skill was dropped (pronunciation scoring was
    its point; the Claude API takes no audio).
    - **Engine: Piper** (`piper-tts` 1.8, GPL-3.0 like the spaCy model; onnxruntime was
      already installed via Chroma). Free and offline, so anyone can run the project.
    - **Voices** (`speech.VOICES`, pinned to rhasspy/piper-voices commit `c10ece1`, model
      SHA-256 checked on download into `data/raw/piper/`):
      - `mx` (default; Jason learns Mexican Spanish): `es_MX-claude-high` (Apache-2.0)
        with noise 0.333/0.333 instead of Piper's 0.667/0.8. At the defaults both Mexican
        voices (claude-high, ald-medium) sounded distorted and robotic to Jason; of four
        tuning variants on three sentences he chose "less noise". Faster speech didn't
        help; no clipping anywhere (≤0.004% of samples near full scale).
      - `es`: `es_ES-davefx-medium` (CC0), defaults ("sounds like a real person").
      - Not chosen: es_ES-sharvard (CC BY 3.0, needs attribution); es_AR is the only other
        Latin American voice.
    - **Measured on Jason's CPU:** a voice loads in ~1.5-2 s; synthesis ~26× real time
      (a 112-char reply in 0.27 s). So audio is made on demand, a sentence at a time, and
      there is no disk cache (the roadmap's first idea); the browser keeps clips for a day
      (`SPEECH_CACHE`). Each synthesis differs slightly (Piper's noise: 63,020 vs 63,532
      bytes for the same sentence).
    - **API:** `GET /api/speech?text=&accent=mx|es` → `audio/wav` (422 empty / >1,500
      chars / unknown accent; 503 voice missing, with the download command);
      `GET /api/speech/voices` (which accents are installed). No database, no lock;
      `speech.Speaker` loads each voice on first use, one lock per voice.
    - **Nothing is logged** to the word bank: hearing isn't evidence of recognition.
    - **Web:** `state/speech.tsx` (provider, one shared player: a new clip stops the
      current one) + `state/speechContext.ts`; settings in the sidebar (accent, read
      replies aloud, speed 1×/0.75× via `playbackRate`), kept in localStorage like the
      theme. Tutor replies that arrive in this visit (`ChatItem.live`) are read aloud
      once; transcripts rebuilt after a reload never are. ▶ on every tutor message and
      on lesson cards (word, example); a clicked word is said as written. The reader
      narrates with `lib/useNarration.ts`: one unit at a time (prose sentences, poem
      lines), highlighted (`SpanishText` `units`/`active`), the next one prefetched,
      pauses 150 ms / 350 ms (line, paragraph) / 900 ms (stanza); a ▶ per paragraph
      starts there; anything else that plays (a clicked word) pauses it in place.
    - **Checked:** 13 pytest (+2 slow on the real voices), 15 Vitest, and
      `web/e2e/listening.mjs` in headless Edge on a database copy (the highlight moves
      as each sentence ends; a clicked word is spoken and pauses the narration; the
      accent toggle switches the voice; a new conversation's opening is read aloud).
    - Not done: the CLI has no audio; the lyrics Compare step has no per-line ▶ (the
      Read tab narrates the poem).
- **Content index and recommender (roadmap Phase 3, built 2026-10-05; Jason's decisions):**
  - **Schema (migration 006; Jason approved the SQL as proposed):** `books`;
    `content_items` (song / story / chapter; a chapter has `book_id` + `chapter_no` and
    takes author, source and `is_private` from its book, enforced by a CASE check; the
    text in `text_es`, last column); `content_vocab` (the difficulty index: content_id,
    lexeme_id, occurrences; WITHOUT ROWID); `content_events` (append-only started /
    finished, `chosen_via` recommended/requested on starts, optional session_id);
    `word_resolutions` (append-only model/human decisions on unknown forms, also the
    cache); `lexemes.example_en_source` (who translated the example; NULL = Tatoeba's
    human translation). No change to `sessions`.
  - **Two recommenders (Jason):** songs and stories by **distinct new words** (the
    pre-teaching burden), ties by coverage; books by **new-word density over the whole
    book** (unknown running words / all), so an easy preface can't make a hard book look
    easy (a test). A started book's next chapter (first after the highest *finished*)
    always comes before a new book; among started books the most recently read leads;
    finished and partly indexed books are left out. "Known" = recognition word bank.
    Queries: `sql/queries/recommend_items.sql`, `recommend_books.sql`,
    `item_new_words.sql`; wrappers in `recommend.py`. `SUM(CASE …)`, not `FILTER`, for
    portability.
  - **Topics stay out of Phase 3 (Jason):** free text with on-the-fly pre-teaching.
  - **"What next?" screen and reader (U2, built 2026-10-05; Jason's choices):**
    - API: `GET /api/recommend` (both rankings), `GET /api/recommend/surprise`,
      `GET /api/content` (catalog: every item with new words and reading state,
      `content_catalog.sql`), `GET /api/content/{id}` (text, book position, state,
      new words; `content_item.sql`), `POST /api/content/{id}/start` (`chosen_via`) and
      `/finish`. All on per-request connections; none calls the model.
    - The page shows two defaults side by side, the easiest song/story and the first book
      (a started book's next chapter), since the rankings use different measures; then
      both ranked tables and "Choose anything" (every item, any chapter).
    - `chosen_via`: `recommended` for a default (a card, or a table's first row) or a
      surprise; `requested` for any other pick.
    - **Surprise me (Jason):** random among the 5 easiest songs/stories plus every started
      book's next chapter; never the start of a new book (`recommend.surprise`).
    - **Simple reader (Jason), until Phase 4:** the item's new words (most frequent in it
      first; 20 shown, "show all"), then the text (prose paragraphs rejoined), and
      Finished, which logs `finished` and returns to the suggestions.
  - **Indexing (`content.py`):** `analyze()` on sentences (songs by line; prose by
    paragraph with hard-wrapped lines rejoined, split after final punctuation and before
    an opening ¿/¡ that follows it). Counts are per analysis (*del* = *de* + *el*; an
    expression once; names not counted). Resolution order: lexicon (accent fallback) →
    Wiktionary or an approved expression (added) → latest `word_resolutions` row for
    (form, tagged lemma, POS) → pending, sent to the resolver. Nothing is written until
    analysis and the model call are done; then one transaction replaces the item's
    index. CLI: `python -m spanish_tutor.content add-song|add-story|add-book|index|list`;
    `index` shows a cost estimate and asks before each model call (`--yes`,
    `--no-resolve`), and writes `data/processed/content_resolutions.csv` (overwritten
    per run).
  - **The resolver (`resolve.py`; Jason: Opus 5.5, variants map to existing words,
    model-written definitions labeled):** one structured call per item (60 forms per
    request), each form with its sentence and the tagger's guess. Verdicts: `variant`
    (counted as the existing word), `word` (added with `definition_source =
    'model:<id>'`, the real sentence as example, Claude's translation with
    `example_en_source`), `not_spanish` (excluded). Every claim is checked: forms not
    asked about are ignored; a POS outside the schema is rejected (AUX folded to VERB
    first); a variant of a word that exists nowhere is rejected; a "new" word that
    already exists is recorded as a variant. Rejected forms aren't stored, so a re-index
    asks again. Lesson cards show a "model-written" tag (`Lesson.model_written`,
    `LessonOut`).
  - **Pre-1952 spellings (Jason):** Wiktionary lists *fué*, *dió*, *fuí*, *vió* (11
    headwords, "deprecated in 1952") as their own entries, so they became words.
    `AccentRestorer` now respells them in the text before tagging
    (`Wiktionary.reform_1952_spellings`). 2010-reform spellings (*sólo*, *guión*) are left
    alone: still common, and in the word bank.
  - **Books from Gutenberg:** `python -m spanish_tutor.ingest.gutenberg <ebook>` downloads
    to `data/raw/gutenberg/` and writes one file per chapter. The splitter reads the
    `#HEADING#` markup of 13507; other books may need another splitter (Phase 4).
  - **Measured on Quiroga, *Cuentos de amor de locura y de muerte* (Gutenberg 13507,
    18 stories), on a DB copy (2026-10-05):**
    - ~47k counted running words, indexed in ~2 min (CPU, nearly all tagging).
    - Before the model: 249 distinct unknown forms (0.69% of running words). Model run:
      **$0.62** (estimate $0.57–0.70; 45k in / 22k out tokens): 143 variants, 77 new
      words, 9 not Spanish, 20 rejected; unresolved fell to 0.08%. A random 30 of the
      accepted verdicts all checked out by hand.
    - The book is far above the word bank: 28% of running words unknown, 4,284 distinct
      new words (comfortable reading needs ~98% coverage).
    - Ranking: 12 ms on the book; under 1 s at 6,000 items × 500 words (3M index rows).
    - Live resolver test (`tests/test_resolve_live.py`, ~1.2¢): *fué* → *ser*, *pa'* →
      *para*, *yeah* not Spanish, *parrandeo* a new word. The estimate constants in
      `content.py` come from it.
  - **Also fixed:** approved expressions missing from the lexicon (43, never in
    Tatoeba) couldn't be added anywhere, since Wiktionary has no EXPR entries;
    `ensure_lexeme` / `LexiconIndex` now take the expressions' reviewed definitions
    (`ingest.expressions.definitions()`), also in conversations.
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
    tagged as proper nouns (*Sales a las ocho*, *Juego al fútbol*) and dropped.
  - **Form-of check (`_relink`, added 2026-10-02):** a tagger lemma that *is* a real word
    is replaced when the form is listed only under other words with that POS.
    - Example: the verb *riego* was lemmatized *regir* by both spaCy models; it's a form
      of *regar*. Other cases: *verte* → *ver*, *buena* → *bueno* (not *buen*),
      *cuántas* → *cuánto*.
    - Scope, from measurement on the md build: VERB, ADJ, DET. Not PRON: Wiktionary
      files *eso* under the old spelling *ése*, so 13k tokens would all go wrong. Not
      NOUN: its hits were mostly spelling variants.
    - VERB guards: only infinitive candidates (participle entries like *hecho* don't
      replace *hacer*). A pronominal entry of the same verb (*quejarse* for *quejar*)
      doesn't split it.
    - Measured on the transformer build: ~3,800 tokens; a token-weighted hand check of 50
      hits found 46 right (misses: rare regional/archaic forms *podes*, *plega*, *retiñe*).
      Scope kept as is.
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
  - **Referenced rows are never deleted,** so `lexeme_id`s and the events referencing
    them survive rebuilds. Frequencies are overwritten; definitions and examples only fill
    blanks (examples move as a unit).
  - **Rows a rebuild no longer produces (Jason's choice, 2026-10-02):** deleted if no
    `word_events` or `lexeme_reviews` row references them. Otherwise they're kept with
    the frequency set to NULL.
    - The first run removed 8 leftovers (*tambien* PRON, *razon*, *había* VERB…) and
      cleared *mas* ADV.
    - A new table referencing `lexemes` must be added to the DELETE. If it isn't, the
      foreign key fails the build and rolls it back (tested).
    - `fill_lexicon` runs the file statement by statement (`db.statements`) to keep one
      transaction and report counts.
    - Consequence to remember: rows inserted by anything other than the build (a future
      PCIC/`cefr_level` import, an `ensure_lexeme` word with no event yet) are deleted by
      the next build unless something references them.
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
  - The tagger's `spacy-transformers` 1.4 further caps `transformers` below 4.53.3 (locked
    at 4.53.2). Checked 2026-10-02: 2,000 stored vectors re-embedded on 4.53.2 have cosine
    1.00000000 to the originals, and the slow tests pass, so no re-index was needed.
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
