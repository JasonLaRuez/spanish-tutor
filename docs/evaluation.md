# Evaluating a vocabulary-constrained Spanish tutor

This is a case study of how the tutor in this repository is evaluated: what it claims to do,
how each claim is measured, what the measurements found, and what they changed. Every
number below can be re-created, without any model calls, by `notebooks/03_evaluation.ipynb`;
the commands that made the judgments are `python -m spanish_tutor.evaluation …`.

**In short:** the tutor's core promise holds (every reply stays within the learner's
vocabulary, and every new word is taught). A model judge of translation quality turned out
to be *reliable* but not *valid*: it agreed with itself (Krippendorff's alpha 0.85) and not
with the learner. And measuring retrieval led to a design change that raised the share of
the tutor's claims supported by its context from 48% to 97%. The whole evaluation cost
about $2.57 in model calls.

## What is being evaluated

The tutor is a set of LLM-backed skills (conversation, reading, song lyrics) built around a
word bank: a log of every Spanish word the learner has been taught or has used. It makes
four claims, each of which can fail:

1. **Replies stay inside the vocabulary.** At most one word the learner doesn't know per
   reply (the *i+1* rule from Krashen's input hypothesis), and that word is taught.
2. **Grading is fair.** A word the learner uses is credited; a wrong *form* of the word they
   meant (*bonita* for *bonito*) is credited at a lower grade; a wrong *word* (*jugo*, juice,
   for *juego*, I play) never is.
3. **Translations are natural.** The lyrics skill gives a natural and a literal translation
   of every line.
4. **Talk about a text is faithful to it.** After reading, the tutor discusses the text
   using what it retrieved from it.

## Principles

- **Metrics are SQL over the log.** Adherence, completeness and the reading and
  recommendation metrics are hand-written queries (`sql/queries/eval_*.sql`) over the
  tables the app writes. The same queries run on the learner's real history and on a
  benchmark's database copy.
- **Small samples are reported as small.** With two real conversations so far, every rate
  carries its *n* and a 95% Wilson interval (not the normal approximation, which breaks
  near 0 and 1). "25 of 25" is reported as "95% CI 87-100%".
- **A judge is checked twice before its numbers are used.** First against itself: every
  judgment is repeated five times and agreement is measured with Krippendorff's alpha
  (implemented in `evaluation/agreement.py` and checked against the worked example in
  Krippendorff's paper). Then against a person: the learner rated a sample blind.
- **Every judgment is kept.** Model and human ratings go in one append-only table, so
  consistency and calibration are self-joins, and a changed rating adds a row.
- **Off-the-shelf where it fits, custom where it doesn't.** RAGAS's retrieval metrics are
  the right definitions, but RAGAS can't be installed next to this project's pinned models
  (it needs `huggingface-hub` ≥ 1.0; the pinned `transformers` needs < 1.0), so the two
  metrics are implemented directly.

## 1. Adherence and completeness, from the real log

| Measure | Result | 95% CI |
|---|---|---|
| Replies within the one-new-word limit | 25 / 25 | 87-100% |
| Replies with a new word that taught all of them | 5 / 5 | 57-100% |

Instrumenting this found a measurement bug before it mattered: a word looked up during a
conversation was logged on the tutor's last reply, where completeness would read it as
over-teaching. Lookups now get a turn of their own.

## 2. Grading, on a benchmark with planted mistakes

The log fills slowly, so a benchmark scripts 40 learner messages across 8 topics: 24 with one
planted mistake (14 wrong forms, 10 wrong words: *ser* for *estar*, real-word misspellings,
English words) and 16 correct, run through the real tutor on a copy of a frozen snapshot of
the word bank.

| Measure | Result |
|---|---|
| Planted mistakes flagged | 24 / 24 |
| ... as the right kind (form vs word) | 24 / 24 |
| Correct messages with no false alarm | 16 / 16 |
| Replies within the limit | 48 / 48, no rewrites needed |

The traps held: *pero* was flagged only where it meant *perro*, and a correct *es muy
alto* was left alone beside a wrong *es enfermo*. The honest reading is that the set is too
easy to find the grader's limits: it is a regression test, and a harder version (subjunctive,
*por/para*, two mistakes in one message) is future work.

## 3. Is a word taught as new really new?

The learner rated the 13 words the tutor taught as new: 8 were new (**62%**, 95% CI 36-82%).
None was a tagging error. The 5 known words were a gap in the word bank, not the detector:
four rank between 1,821 and 2,656 by frequency, just past the 1,500 words the initial
vocabulary review covered, and one was skipped during that review. The gap closes itself as
words are taught; a review of ranks 1,501-3,000 would close it faster.

## 4. Can a model judge translations?

Claude Sonnet 5.5 scored the natural translation of 88 lines (10 poems) for naturalness and
faithfulness, 1-5, five times each.

**Consistency.** In a pilot the judge wobbled between 4 and 5 on whether supplying a word the
Spanish only implies ("For one glance, *I'd give* a world") was faithful. One rubric sentence
("English needs many words that Spanish leaves unsaid: supplying a word the Spanish implies
is faithful") ended it: that line went from 5, 4, 4, 5, 5 to five 5s. In the full run both
criteria cleared alpha 0.80, the level Krippendorff calls reliable (naturalness 0.851,
faithfulness 0.824).

The app's comparison of a learner's own attempt with the translation was also consistent:
18 attempts written at a known quality, five times each, never changed verdict, and 85 of 90
matched the intended one. The 5 misses are one attempt, "Do you know when it comes back?"
for *¿Sabes tú adónde va?* ("Do you know where it goes?"), called "close" every time even
though its own comment says the meaning is wrong. A consistent judge can be consistently
wrong.

**Calibration.** The learner, a native English speaker, rated 28 lines without seeing the
judge's scores:

| | Learner | Judge (median of 5) |
|---|---|---|
| Mean naturalness | 4.86 | 3.93 |
| Lines rated lower by the judge / higher | | 21 / 1 |
| Within one point | | 23 / 28 |
| Agreement on which lines are worse (alpha) | | −0.37 |

The judge is a point stricter, and it marks down lines that are fragments of a sentence,
which the learner reads as natural in the poem. A rubric revision to read each line with
its neighbours made it *worse* (mean 3.62, alpha −0.52): asked to judge whole sentences, it
scored a 19th-century poet's inverted syntax as convoluted English, an editor's standard
rather than a reader's. The revision was dropped and kept in the log as evidence.

**Conclusion:** the judge's naturalness scores are reliable but not valid for this user, and
an evaluation that stopped at consistency would have reported them. Poems are also the
hardest case for line-by-line translation and only the public-domain demo, so calibration
will be redone on song lyrics; until then, naturalness is reported from the learner's own
ratings (4.86 / 5, n = 28).

## 5. Retrieval

**Context relevance.** Each conversation reply is given up to four example sentences the
learner can fully read, retrieved from 261,000 Tatoeba sentences. For the benchmark's 40
messages, 67% of the 159 sentences retrieved were relevant to the message and 99% at least
partly.

**Faithfulness.** After reading, the tutor discussed the text using the three passages
retrieved for each learner message. Two scripted discussions (10 questions, two of them traps
the text doesn't answer) were checked claim by claim:

| | Retrieved passages | Whole text |
|---|---|---|
| The context holds the answer | 5 / 10 | 10 / 10 |
| Claims supported by what the tutor was given | 15 / 31 (48%) | 32 / 33 (97%) |
| Traps answered honestly ("the story doesn't say") | 2 / 2 | 2 / 2 |

The tutor never invented a fact; retrieval was the weak link, because three-sentence
passages found by similarity to a short question often missed the part that answers it.
The texts were short (about 400 words), so retrieval added risk and saved nothing. **Texts up
to 2,000 words now go into the discussion's cached prompt whole** (about 2.4¢ per discussion
at most), and retrieval is kept for longer ones. Re-measured, faithfulness rose to 97%.

## What the evaluation changed

- Lookups are logged on their own turn (a measurement bug, found while instrumenting).
- The translation rubric gained one sentence that removed a judge's inconsistency.
- Short readings are discussed with the whole text instead of retrieved passages.
- A data bug surfaced: margin line numbers left in a book chapter (and read aloud by the
  narrator); the book's clean-up pattern now catches them.
- The roadmap gained an item: difficulty beyond vocabulary. A poem can pass the vocabulary
  ceiling while its syntax makes it far harder than prose.

## Limitations

- Two real conversations: the log metrics' intervals are wide until the learner uses the
  app more, and the reading and recommendation metrics have no data yet.
- The grading benchmark is too easy to show where grading fails.
- One rater: calibration rests on one learner's 28 ratings, on poems.
- Faithfulness and relevance use a single judgment per item, and faithfulness is unvalidated
  against a fluent Spanish reader.

## Reproducing

```sh
uv run python -m spanish_tutor.evaluation report                  # log metrics, read-only
uv run python -m spanish_tutor.evaluation freeze                  # the benchmark's snapshot, once
uv run python -m spanish_tutor.evaluation benchmark               # ~80¢
uv run python -m spanish_tutor.evaluation translations            # ~85¢
uv run python -m spanish_tutor.evaluation calibration 3           # judge run vs the learner
uv run python -m spanish_tutor.evaluation retrieval               # ~35¢
uv run jupyter nbconvert --to notebook --execute notebooks/03_evaluation.ipynb
```
