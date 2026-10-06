import type { Summary } from '../api/client'
import { TodaysWords } from './TodaysWords'

const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? '' : 's'}`

/** The end of a conversation: what happened, in numbers, and the tutor's notes. */
export function SummaryCard({ summary }: { summary: Summary }) {
  const stats: [string, string | number][] = [
    ['Minutes', summary.minutes ?? '–'],
    ['Messages', summary.messages],
    ['Corrections', summary.corrections],
    ['¿Cómo se dice?', summary.how_to_say],
    ['Words used', summary.words_used],
    ['Words taught', summary.words_taught],
  ]
  return (
    <section
      aria-label="Conversation summary"
      className="space-y-4 rounded-xl border border-line bg-surface p-5"
    >
      <h2 className="text-lg font-semibold text-ink">Resumen</h2>

      <dl className="grid grid-cols-3 gap-3 sm:grid-cols-6">
        {stats.map(([label, value]) => (
          <div key={label}>
            <dt className="text-xs text-muted">{label}</dt>
            <dd className="text-xl font-semibold text-ink">{value}</dd>
          </div>
        ))}
      </dl>

      {summary.first_time.length > 0 && (
        <div className="space-y-1">
          <h3 className="text-sm font-medium text-ink">
            Used for the first time: {count(summary.first_time.length, 'word')}
          </h3>
          <p className="es text-ink-2">{summary.first_time.join(', ')}</p>
        </div>
      )}

      {summary.pre_taught.length > 0 && (
        <div className="space-y-1.5">
          <h3 className="text-sm font-medium text-ink">
            Today’s words: {summary.pre_taught_used.length} of {summary.pre_taught.length} used
          </h3>
          <TodaysWords focus={summary.pre_taught} used={summary.pre_taught_used} />
          {summary.practice.length > 0 && (
            <p className="text-sm text-ink-2">
              Words you knew but had never used: {summary.practice_first_use.length} of{' '}
              {summary.practice.length} used for the first time
              {summary.practice_first_use.length > 0 && (
                <>
                  {' '}
                  (<span className="es">{summary.practice_first_use.join(', ')}</span>)
                </>
              )}
              .
            </p>
          )}
        </div>
      )}

      {summary.went_well_en ? (
        <div className="space-y-2 border-t border-line pt-4">
          <h3 className="text-sm font-medium text-ink">Tutor’s notes</h3>
          <p className="text-ink-2">
            <span className="font-medium text-ink">Went well: </span>
            {summary.went_well_en}
          </p>
          {summary.work_on.length > 0 && (
            <div>
              <p className="font-medium text-ink">Work on:</p>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-ink-2">
                {summary.work_on.map((point) => (
                  <li key={point}>{point}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      ) : (
        summary.notes_error && (
          <p className="text-sm text-muted">
            The tutor’s notes couldn’t be written ({summary.notes_error}). The numbers above
            are complete.
          </p>
        )
      )}
    </section>
  )
}
