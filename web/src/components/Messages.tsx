import { useState, type ReactNode } from 'react'
import type { Lesson, Turn } from '../api/client'
import { splitWords } from '../lib/text'
import { LessonCard, LessonList } from './LessonCard'

type LookUp = (word: string) => Promise<Lesson>

/** Spanish text whose words can be clicked to look them up (when `onLookUp` is given).
 *  Words in `marked` (lowercase) are underlined: in the reader, the words still to study. */
export function SpanishText({
  text,
  onLookUp,
  marked,
}: {
  text: string
  onLookUp?: LookUp
  marked?: Set<string>
}) {
  const [lookup, setLookup] = useState<
    { word: string; lesson?: Lesson; error?: string; loading?: boolean } | null
  >(null)

  const open = async (word: string) => {
    if (!onLookUp) return
    setLookup({ word, loading: true })
    try {
      setLookup({ word, lesson: await onLookUp(word) })
    } catch (error) {
      setLookup({ word, error: error instanceof Error ? error.message : String(error) })
    }
  }

  return (
    <>
      <p className="es whitespace-pre-wrap">
        {splitWords(text).map((part, i) =>
          part.word && onLookUp ? (
            <button
              key={i}
              type="button"
              onClick={() => open(part.text)}
              className={`cursor-help rounded-sm underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-accent ${
                marked?.has(part.text.toLowerCase())
                  ? 'underline decoration-accent decoration-2'
                  : 'decoration-dotted decoration-1'
              }`}
              title={`Look up “${part.text}”`}
            >
              {part.text}
            </button>
          ) : (
            <span key={i}>{part.text}</span>
          ),
        )}
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

function Bubble({ children, label }: { children: ReactNode; label: string }) {
  return (
    <div className="max-w-[44rem]" aria-label={label}>
      <div className="rounded-2xl rounded-tl-md border border-line bg-surface px-4 py-3 text-ink shadow-xs">
        {children}
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
        <Bubble label="Translation">
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
      <Bubble label="Tutor">
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
