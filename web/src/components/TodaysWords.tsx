import type { Lesson } from '../api/client'
import { LessonList } from './LessonCard'
import { Note } from './Messages'

/** Today’s topic words as a checklist: each is checked once the learner has used it. */
export function TodaysWords({ focus, used }: { focus: string[]; used: string[] }) {
  const usedSet = new Set(used)
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Today’s words">
      {focus.map((word) => {
        const done = usedSet.has(word)
        return (
          <li
            key={word}
            className={`es rounded-full border px-2.5 py-0.5 text-sm ${
              done ? 'border-accent bg-accent-soft text-ink' : 'border-line text-ink-2'
            }`}
          >
            <span aria-hidden>{done ? '✓ ' : ''}</span>
            {word}
            <span className="sr-only">{done ? ' (used)' : ' (not used yet)'}</span>
          </li>
        )
      })}
    </ul>
  )
}

/** The words taught before the conversation, with why there are fewer than asked for. */
export function PreTaught({
  lessons,
  requested,
  shortfall,
}: {
  lessons: Lesson[]
  requested: number
  shortfall: string | null
}) {
  return (
    <div className="space-y-2">
      {shortfall && (
        <Note title={`${lessons.length} of ${requested} words`}>{shortfall}</Note>
      )}
      {lessons.length > 0 && (
        <>
          <LessonList lessons={lessons} title="Words for today" />
          <p className="text-sm text-ink-2">
            Try to use these in your replies. “Today’s words” at the top checks each one off
            when you do.
          </p>
        </>
      )}
    </div>
  )
}
