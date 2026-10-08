import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, type ComparedLine, type Expression, type Lesson, type ReadingState, type StudyWord } from '../api/client'
import { LessonCard } from '../components/LessonCard'
import { CompareLines, TryLines } from '../components/Lyrics'
import { loadComparison } from '../lib/lyrics'
import { useNarration } from '../lib/useNarration'
import { SpanishText } from '../components/Messages'
import { useConversations } from '../state/context'
import { useSpeech } from '../state/speechContext'

type Step = 'study' | 'try' | 'compare' | 'read'

const STEP_NAMES: Record<Step, string> = { study: 'Study', try: 'Try first', compare: 'Compare', read: 'Read' }

/** The reading skill: study the text's new words in batches (in the order they appear),
 *  read it with click-to-look-up, mark it finished, then talk about it with the tutor.
 *  A song or poem (the lyrics skill) adds two steps: translate some lines yourself, then
 *  compare with natural and literal translations. */
export function ReadingPage() {
  const sessionId = Number(useParams().sessionId)
  const navigate = useNavigate()
  const { discuss } = useConversations()
  const [state, setState] = useState<ReadingState | null>(null)
  const [batch, setBatch] = useState<StudyWord[] | null>(null)
  const [step, setStep] = useState<Step>('study')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [comparison, setComparison] = useState<{ lines: ComparedLine[]; expressions: Expression[] } | null>(null)

  const fail = (err: unknown) => setError(err instanceof Error ? err.message : String(err))

  useEffect(() => {
    api
      .reading(sessionId)
      .then((loaded) => {
        setState(loaded)
        setStep(loaded.remaining > 0 && !loaded.finished ? 'study' : 'read')
      })
      .catch(fail)
  }, [sessionId])

  // The next batch, whenever the study step needs one.
  useEffect(() => {
    if (step !== 'study' || !state || state.remaining === 0 || batch) return
    api
      .readingBatch(sessionId)
      .then((next) => {
        setBatch(next.words)
        setState(next.state)
      })
      .catch(fail)
  }, [step, state, batch, sessionId])

  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    try {
      await action()
    } catch (err) {
      fail(err)
    } finally {
      setBusy(false)
    }
  }

  const studyBatch = () =>
    run(async () => {
      setState(await api.readingStudy(sessionId, (batch ?? []).map((w) => w.lexeme_id)))
      setBatch(null)
    })

  const lookUp = useCallback(
    async (word: string) => {
      const found = await api.readingLookUp(sessionId, word)
      setState(found.state)
      return found.lesson
    },
    [sessionId],
  )

  // The translation is made the first time a song is opened (a model call), then stored.
  const showTranslations = () => run(async () => setComparison(await loadComparison(sessionId)))
  const compare = (attempts: { line_no: number; text: string }[]) =>
    run(async () => {
      const translation = comparison ?? (await loadComparison(sessionId))
      const compared = await api.attempt(sessionId, attempts)
      setComparison({ lines: compared.lines, expressions: translation.expressions })
      setStep('compare')
    })

  const finish = () => run(async () => setState(await api.readingFinish(sessionId)))
  const talk = () => run(async () => navigate(`/chat/${await discuss(sessionId)}`))

  if (error && !state) return <p className="p-8 text-danger">{error}</p>
  if (!state) return <p className="p-8 text-muted">Opening the text…</p>

  const studied = state.total_new - state.remaining
  const lyrics = state.skill === 'lyrics'
  const steps: Step[] = lyrics ? ['study', 'try', 'compare', 'read'] : ['study', 'read']
  const kindName = { song: 'Song', poem: 'Poem', story: 'Story', chapter: 'Chapter' }[state.kind]
  const where =
    state.kind === 'chapter' ? `${state.book_title} · chapter ${state.chapter_no} of ${state.chapters}` : kindName

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-6 py-10">
      <div>
        <Link to="/next" className="text-sm text-accent-text hover:underline">
          ← What next?
        </Link>
        <h1 lang="es" className="mt-3 text-2xl font-semibold text-ink">
          {state.title}
        </h1>
        <p className="mt-1 text-sm text-muted">
          {where}
          {state.author ? ` · ${state.author}` : ''}
          {state.finished ? ' · finished' : ''}
        </p>
      </div>

      <section aria-label="Study progress" className="space-y-1.5">
        <div className="flex items-baseline justify-between text-sm">
          <span className="text-ink-2">
            {state.total_new === 0
              ? 'You know every word in it.'
              : `Studied ${studied.toLocaleString()} of ${state.total_new.toLocaleString()} new words`}
          </span>
          {state.total_new > 0 && <span className="tabular text-muted">{Math.round((studied / state.total_new) * 100)}%</span>}
        </div>
        {state.total_new > 0 && (
          <div className="h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden>
            <div className="h-full rounded-full bg-accent" style={{ width: `${(studied / state.total_new) * 100}%` }} />
          </div>
        )}
      </section>

      <div role="tablist" className="flex gap-1 border-b border-line">
        {steps.map((tab) => (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={step === tab}
            onClick={() => setStep(tab)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${
              step === tab ? 'border-accent font-medium text-ink' : 'border-transparent text-ink-2 hover:text-ink'
            }`}
          >
            {tab === 'study' && state.remaining ? `Study (${state.remaining.toLocaleString()})` : STEP_NAMES[tab]}
          </button>
        ))}
      </div>

      {error && <p className="text-danger">{error}</p>}

      {step === 'study' && (
        <Study
          state={state}
          batch={batch}
          busy={busy}
          onStudy={studyBatch}
          onRead={() => setStep(lyrics ? 'try' : 'read')}
          readLabel={lyrics ? 'Try translating' : 'Start reading'}
        />
      )}
      {step === 'try' && <TryLines state={state} busy={busy} onCompare={compare} />}
      {step === 'compare' &&
        (comparison ? (
          <CompareLines lines={comparison.lines} expressions={comparison.expressions} />
        ) : (
          <section className="space-y-3 rounded-xl border border-line bg-surface p-5">
            <p className="text-sm text-ink-2">
              Try a few lines yourself first, or see the natural and literal translations now.
            </p>
            <button
              type="button"
              onClick={showTranslations}
              disabled={busy}
              className="rounded-lg border border-line bg-surface px-4 py-2 text-sm font-medium text-ink hover:border-accent disabled:opacity-50"
            >
              {busy ? 'Translating…' : 'Show the translations'}
            </button>
          </section>
        ))}
      {step === 'read' && <Read state={state} onLookUp={lookUp} />}

      <div className="flex flex-wrap items-center gap-3 border-t border-line pt-5">
        {!state.finished ? (
          <>
            <button
              type="button"
              onClick={finish}
              disabled={busy}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-ink disabled:opacity-50"
            >
              Finished
            </button>
            <span className="text-sm text-ink-2">Mark it read; then you can talk about it.</span>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={talk}
              disabled={busy}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-ink disabled:opacity-50"
            >
              Talk about it
            </button>
            <Link to="/next" className="text-sm text-accent-text hover:underline">
              Back to your suggestions
            </Link>
          </>
        )}
      </div>
    </div>
  )
}

function Study(props: {
  state: ReadingState
  batch: StudyWord[] | null
  busy: boolean
  onStudy: () => void
  onRead: () => void
  readLabel: string
}) {
  if (props.state.remaining === 0) {
    return (
      <section className="space-y-3 rounded-xl border border-line bg-surface p-5">
        <h2 className="font-semibold text-ink">Every new word is studied</h2>
        <p className="text-sm text-ink-2">You can read the whole text with words you know.</p>
        <button
          type="button"
          onClick={props.onRead}
          className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-ink"
        >
          {props.readLabel === 'Start reading' ? 'Read it now' : props.readLabel}
        </button>
      </section>
    )
  }
  if (!props.batch) return <p className="text-muted">Preparing the next words…</p>
  return (
    <section aria-label="Words to study" className="space-y-4">
      <p className="text-sm text-ink-2">
        The next {props.batch.length} new words, in the order they appear in the text. Studying
        them adds them to the words you recognize. You can start reading at any time.
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        {props.batch.map((word) => (
          <div key={word.lexeme_id} className="min-w-0 space-y-1">
            <LessonCard lesson={word.lesson} />
            <p lang="es" className="px-1 text-xs text-muted">
              In the text: {word.context}
            </p>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-3">
        <button
          type="button"
          onClick={props.onStudy}
          disabled={props.busy}
          className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-ink disabled:opacity-50"
        >
          I’ve studied these
        </button>
        <button
          type="button"
          onClick={props.onRead}
          className="rounded-lg border border-line bg-surface px-4 py-2 text-sm font-medium text-ink hover:border-accent"
        >
          {props.readLabel}
        </button>
      </div>
    </section>
  )
}

function Read({
  state,
  onLookUp,
}: {
  state: ReadingState
  onLookUp: (word: string) => Promise<Lesson>
}) {
  const marked = useMemo(() => new Set(state.unstudied), [state.unstudied])
  // Where the fully studied part ends: before the first paragraph with a sentence past it.
  let sentence = 0
  let markerBefore = -1
  state.paragraphs.forEach((paragraph, i) => {
    if (markerBefore < 0 && sentence + paragraph.length > state.readable_until) markerBefore = i
    sentence += paragraph.length
  })
  const verse = state.kind === 'song' || state.kind === 'poem'
  const join = verse ? '\n' : ' '

  // Narration reads the text's units (sentences, or a poem's lines) one at a time; each
  // paragraph (stanza) starts at an offset into that flat list, and its last unit ends it.
  const { units, starts, breaks } = useMemo(() => {
    const starts: number[] = []
    const breaks = new Set<number>()
    let n = 0
    for (const paragraph of state.paragraphs) {
      starts.push(n)
      n += paragraph.length
      breaks.add(n - 1)
    }
    return { units: state.paragraphs.flat(), starts, breaks }
  }, [state.paragraphs])
  const narration = useNarration(units, verse, breaks)
  // Copyrighted items (private songs) aren't read aloud in full; single words still are.
  const speech = useSpeech()
  const available = speech.available && !state.is_private
  const kinds = new Set(state.marked.flatMap((line) => Object.values(line)))

  return (
    <section aria-label="Text" className="space-y-4">
      <p className="text-sm text-ink-2">
        {state.remaining > 0
          ? 'Underlined: words you haven’t studied yet. Click any word to look it up.'
          : 'Click any word to look it up.'}
      </p>
      {kinds.size > 0 && (
        <p className="flex flex-wrap gap-x-4 text-sm text-ink-2" aria-label="Marked words">
          {kinds.has('english') && (
            <span>
              <span lang="en" className="text-english italic">English</span>: the song in English, nothing to learn
            </span>
          )}
          {kinds.has('loanword') && (
            <span>
              <span className="text-loanword underline decoration-dashed">loanword</span>: English used as Spanish
            </span>
          )}
        </p>
      )}
      {available && <NarrationBar narration={narration} total={units.length} verse={verse} />}
      {state.is_private && speech.available && (
        <p className="text-sm text-muted">Narration is off for copyrighted songs. Click a word to hear it.</p>
      )}
      <article className="space-y-4 text-[1.05rem] leading-relaxed text-ink">
        {state.paragraphs.map((paragraph, i) => {
          const active =
            narration.at !== null && narration.at >= starts[i] && narration.at < starts[i] + paragraph.length
              ? narration.at - starts[i]
              : null
          return (
            <div key={i}>
              {i === markerBefore && i > 0 && (
                <p role="separator" className="mb-4 border-t border-dashed border-accent pt-1 text-xs text-accent-text">
                  Every word above is studied
                </p>
              )}
              <div className="group flex gap-1">
                <div className="min-w-0 flex-1">
                  <SpanishText
                    units={paragraph}
                    joiner={join}
                    active={narration.status === 'idle' ? null : active}
                    onLookUp={onLookUp}
                    marked={marked}
                    marks={state.marked.slice(starts[i], starts[i] + paragraph.length)}
                  />
                </div>
                {available && (
                  <button
                    type="button"
                    onClick={() => narration.play(starts[i])}
                    aria-label={`Read aloud from ${verse ? 'stanza' : 'paragraph'} ${i + 1}`}
                    title="Read aloud from here"
                    className="h-6 w-6 shrink-0 rounded-full text-xs text-muted opacity-60 hover:bg-surface-2 hover:text-ink hover:opacity-100 focus-visible:opacity-100"
                  >
                    <span aria-hidden>▶</span>
                  </button>
                )}
              </div>
            </div>
          )
        })}
      </article>
    </section>
  )
}

/** Play, pause, resume or stop the narration, with where it is in the text. */
function NarrationBar({
  narration,
  total,
  verse,
}: {
  narration: ReturnType<typeof useNarration>
  total: number
  verse: boolean
}) {
  const { at, status, play, pause, resume, stop } = narration
  const unit = verse ? 'line' : 'sentence'
  const button = 'rounded-md border border-line px-2.5 py-1 text-sm text-ink-2 hover:bg-surface-2'
  return (
    <div role="group" aria-label="Narration" className="flex flex-wrap items-center gap-2">
      {status === 'idle' && (
        <button type="button" className={button} onClick={() => play(0)}>
          ▶ Read aloud
        </button>
      )}
      {status === 'playing' && (
        <button type="button" className={button} onClick={pause}>
          ❚❚ Pause
        </button>
      )}
      {status === 'paused' && (
        <button type="button" className={button} onClick={resume}>
          ▶ Resume
        </button>
      )}
      {status !== 'idle' && (
        <>
          <button type="button" className={button} onClick={stop}>
            ■ Stop
          </button>
          <span className="text-sm text-muted" aria-live="polite">
            {status === 'paused' ? 'Paused at' : 'Reading'} {unit} {(at ?? 0) + 1} of {total}
          </span>
        </>
      )}
    </div>
  )
}
