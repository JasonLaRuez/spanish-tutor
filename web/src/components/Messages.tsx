import { useState, type ReactNode } from 'react'
import type { Lesson, Turn } from '../api/client'
import { markOf, type LineMarks } from '../lib/marks'
import { splitWords } from '../lib/text'
import { useSpeech } from '../state/speechContext'
import { LessonCard, LessonList } from './LessonCard'
import { SpeakButton } from './SpeakButton'

type LookUp = (word: string) => Promise<Lesson>

/** Spanish text whose words can be clicked to look them up (when `onLookUp` is given);
 *  a clicked word is also said aloud, as written. Words in `marked` (lowercase) are
 *  underlined: in the reader, the words still to study.
 *
 *  The reader passes the text as `units` (sentences, or a poem's lines) joined by
 *  `joiner`, so the one being narrated (`active`) can be highlighted. */
export function SpanishText({
  text,
  units,
  joiner = ' ',
  active = null,
  onLookUp,
  marked,
  marks,
}: {
  text?: string
  units?: string[]
  joiner?: string
  active?: number | null
  onLookUp?: LookUp
  marked?: Set<string>
  marks?: LineMarks[] // per unit: a song line's English words and loanwords
}) {
  const [lookup, setLookup] = useState<
    { word: string; lesson?: Lesson; error?: string; loading?: boolean } | null
  >(null)
  const { say } = useSpeech()

  const open = async (word: string) => {
    if (!onLookUp) return
    say(word)
    setLookup({ word, loading: true })
    try {
      setLookup({ word, lesson: await onLookUp(word) })
    } catch (error) {
      setLookup({ word, error: error instanceof Error ? error.message : String(error) })
    }
  }

  const words = (unit: string, u = 0) =>
    splitWords(unit).map((part, i) => {
      const mark = part.word ? markOf(part.text, marks?.[u]) : undefined
      if (mark === 'english') {
        // The song switching into English: not Spanish vocabulary, so nothing to look up.
        return (
          <span key={i} lang="en" title="English" className="text-english italic">
            {part.text}
          </span>
        )
      }
      return part.word && onLookUp ? (
        <button
          key={i}
          type="button"
          onClick={() => open(part.text)}
          className={`cursor-help rounded-sm underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-accent ${
            marked?.has(part.text.toLowerCase())
              ? 'underline decoration-accent decoration-2'
              : 'decoration-dotted decoration-1'
          } ${mark === 'loanword' ? 'text-loanword underline decoration-dashed decoration-loanword' : ''}`}
          title={mark === 'loanword' ? `English loanword: look up “${part.text}”` : `Look up “${part.text}”`}
        >
          {part.text}
        </button>
      ) : (
        <span key={i} className={mark === 'loanword' ? 'text-loanword' : undefined}>
          {part.text}
        </span>
      )
    })

  return (
    <>
      <p className="es whitespace-pre-wrap">
        {units
          ? units.map((unit, u) => (
              <span key={u}>
                {u > 0 && joiner}
                <span
                  data-unit={u}
                  aria-current={u === active ? 'true' : undefined}
                  ref={u === active ? (node) => node?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' }) : undefined}
                  className={u === active ? 'rounded-sm bg-accent-soft' : undefined}
                >
                  {words(unit, u)}
                </span>
              </span>
            ))
          : words(text ?? '')}
      </p>
      {lookup && (
        <div className="mt-2 space-y-1">
          <div className="flex items-center justify-between text-xs text-muted">
            <span>Look up: {lookup.word}</span>
            <button
              type="button"
              onClick={() => setLookup(null)}
              className="rounded px-1 hover:text-ink"
              aria-label="Close the lookup"
            >
              ✕
            </button>
          </div>
          {lookup.loading && <p className="text-sm text-muted">Looking it up…</p>}
          {lookup.lesson && <LessonCard lesson={lookup.lesson} />}
          {lookup.error && <p className="text-sm text-danger">{lookup.error}</p>}
        </div>
      )}
    </>
  )
}

function Bubble({ children, label, speak }: { children: ReactNode; label: string; speak?: string }) {
  return (
    <div className="max-w-[44rem]" aria-label={label}>
      <div className="flex gap-2 rounded-2xl rounded-tl-md border border-line bg-surface px-4 py-3 text-ink shadow-xs">
        <div className="min-w-0 flex-1">{children}</div>
        {speak && <SpeakButton text={speak} label="Listen to the tutor" />}
      </div>
    </div>
  )
}

/** A correction note (conversation) or explanation (translation), in English. */
export function Note({ children, title }: { children: ReactNode; title: string }) {
  return (
    <div className="mt-2 max-w-[44rem] rounded-lg border border-note-line bg-note px-3 py-2 text-sm text-note-ink">
      <span className="font-medium">{title}: </span>
      {children}
    </div>
  )
}

export function TutorMessage({ turn, onLookUp }: { turn: Turn; onLookUp?: LookUp }) {
  const [english, setEnglish] = useState(false)

  if (turn.kind === 'translation') {
    return (
      <div className="space-y-2">
        <Bubble label="Translation" speak={turn.reply_es}>
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">Se dice</p>
          <SpanishText text={turn.reply_es} onLookUp={onLookUp} />
        </Bubble>
        {turn.note_en && <Note title="Why">{turn.note_en}</Note>}
        {turn.lessons.length > 0 && <LessonList lessons={turn.lessons} title="New words" />}
        {turn.pending && (
          <p className="text-sm text-ink-2">
            <span className="text-muted">Seguimos: </span>
            <span className="es">{turn.pending}</span>
          </p>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <Bubble label="Tutor" speak={turn.reply_es}>
        <SpanishText text={turn.reply_es} onLookUp={onLookUp} />
        {turn.reply_en && (
          <div className="mt-2">
            <button
              type="button"
              onClick={() => setEnglish((shown) => !shown)}
              aria-expanded={english}
              className="text-xs text-accent-text hover:underline"
            >
              {english ? 'Hide English' : 'English'}
            </button>
            {english && <p className="mt-1 text-sm text-ink-2">{turn.reply_en}</p>}
          </div>
        )}
      </Bubble>
      {turn.note_en && <Note title="Note">{turn.note_en}</Note>}
      {turn.not_words.length > 0 && (
        <p className="text-xs text-muted">
          Not recognized as Spanish words: {turn.not_words.join(', ')}
        </p>
      )}
      {turn.lessons.length > 0 && <LessonList lessons={turn.lessons} title="New words" />}
    </div>
  )
}

export function LearnerMessage({ text }: { text: string }) {
  return (
    <div className="flex justify-end" aria-label="You">
      <div className="max-w-[36rem] rounded-2xl rounded-tr-md bg-learner px-4 py-2.5 text-learner-ink">
        <p className="es whitespace-pre-wrap">{text}</p>
      </div>
    </div>
  )
}

export function TypingIndicator() {
  return (
    <div role="status" aria-label="The tutor is writing" className="flex items-center gap-2">
      <div className="flex gap-1 rounded-2xl rounded-tl-md border border-line bg-surface px-4 py-3">
        {[0, 1, 2].map((dot) => (
          <span key={dot} className="typing-dot h-2 w-2 rounded-full bg-muted" />
        ))}
      </div>
      <span className="text-xs text-muted">writing…</span>
    </div>
  )
}
