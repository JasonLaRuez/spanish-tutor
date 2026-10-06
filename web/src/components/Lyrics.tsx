import { useState } from 'react'
import { type ComparedLine, type Expression, type ReadingState } from '../api/client'

const VERDICT: Record<string, { label: string; className: string }> = {
  right: { label: 'Right', className: 'bg-accent-soft text-accent-text' },
  close: { label: 'Close', className: 'bg-note text-note-ink' },
  missed: { label: 'Missed', className: 'bg-surface-2 text-ink-2' },
}

/** Try first: translate some lines into English yourself, before seeing any translation.
 *  The first stanza is chosen to start with; any line can be added or left out. */
export function TryLines({
  state,
  busy,
  onCompare,
}: {
  state: ReadingState
  busy: boolean
  onCompare: (attempts: { line_no: number; text: string }[]) => void
}) {
  const firstStanza = state.paragraphs[0]?.length ?? 0
  const [chosen, setChosen] = useState<Set<number>>(
    () => new Set(Array.from({ length: firstStanza }, (_, i) => i + 1)),
  )
  const [texts, setTexts] = useState<Record<number, string>>({})
  let lineNo = 0
  const attempts = [...chosen].sort((a, b) => a - b).map((n) => ({ line_no: n, text: texts[n] ?? '' }))
  const ready = attempts.some((a) => a.text.trim())

  return (
    <section aria-label="Try first" className="space-y-4">
      <p className="text-sm text-ink-2">
        Translate the chosen lines into English yourself, then compare with a natural and a
        literal translation. It’s practice, not a test: nothing is graded.
      </p>
      <div className="space-y-5">
        {state.paragraphs.map((stanza, s) => (
          <div key={s} className="space-y-2">
            {stanza.map((line) => {
              const n = ++lineNo
              const on = chosen.has(n)
              return (
                <div key={n} className="space-y-1">
                  <label className="flex items-start gap-2">
                    <input
                      id={`try-line-${n}`}
                      type="checkbox"
                      checked={on}
                      onChange={() =>
                        setChosen((old) => {
                          const next = new Set(old)
                          if (next.has(n)) next.delete(n)
                          else next.add(n)
                          return next
                        })
                      }
                      className="mt-1.5 accent-accent"
                    />
                    <span lang="es" className="es text-ink">
                      {line}
                    </span>
                  </label>
                  {on && (
                    <input
                      id={`try-text-${n}`}
                      type="text"
                      aria-label={`Your translation of line ${n}`}
                      value={texts[n] ?? ''}
                      onChange={(event) => setTexts((old) => ({ ...old, [n]: event.target.value }))}
                      placeholder="In English…"
                      className="ml-6 w-[calc(100%-1.5rem)] rounded-lg border border-line bg-surface px-3 py-1.5 text-sm text-ink focus:border-accent focus:outline-none"
                    />
                  )}
                </div>
              )
            })}
          </div>
        ))}
      </div>
      <button
        type="button"
        onClick={() => onCompare(attempts.filter((a) => a.text.trim()))}
        disabled={busy || !ready}
        className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-ink disabled:opacity-50"
      >
        Compare
      </button>
    </section>
  )
}

/** Compare: every line with its natural and literal translations side by side, the note
 *  where they differ, and the learner's attempt with a comment. */
export function CompareLines({
  lines,
  expressions,
}: {
  lines: ComparedLine[]
  expressions: Expression[]
}) {
  return (
    <section aria-label="Compare" className="space-y-4">
      <ol className="space-y-3">
        {lines.map((line) => (
          <li key={line.line_no} className="space-y-2 rounded-xl border border-line bg-surface p-4">
            <p lang="es" className="es font-medium text-ink">
              {line.es}
            </p>
            {line.attempt && (
              <div className="space-y-1 rounded-lg bg-surface-2 px-3 py-2 text-sm">
                <p className="text-ink">
                  <span className="text-muted">You: </span>
                  {line.attempt}
                  {line.verdict && (
                    <span className={`ml-2 rounded px-1.5 py-0.5 text-xs ${VERDICT[line.verdict].className}`}>
                      {VERDICT[line.verdict].label}
                    </span>
                  )}
                </p>
                {line.comment_en && <p className="text-ink-2">{line.comment_en}</p>}
              </div>
            )}
            <div className="grid gap-2 text-sm sm:grid-cols-2">
              <p className="min-w-0">
                <span className="block text-xs font-medium uppercase tracking-wide text-muted">Natural</span>
                <span className="text-ink">{line.natural_en}</span>
              </p>
              <p className="min-w-0">
                <span className="block text-xs font-medium uppercase tracking-wide text-muted">Literal</span>
                <span className="text-ink-2">{line.literal_en}</span>
              </p>
            </div>
            {line.note_en && (
              <p className="rounded-lg border border-note-line bg-note px-3 py-2 text-sm text-note-ink">{line.note_en}</p>
            )}
          </li>
        ))}
      </ol>
      {expressions.length > 0 && (
        <section aria-label="Expressions" className="space-y-2">
          <h2 className="font-semibold text-ink">Expressions in it</h2>
          <ul className="space-y-1.5 text-sm">
            {expressions.map((e, i) => (
              <li key={`${e.phrase}-${i}`}>
                <span lang="es" className="es font-medium text-ink">
                  {e.phrase}
                </span>{' '}
                <span className="text-xs text-muted">line {e.line_no}</span>
                <span className="block text-ink-2">{e.definition_en}</span>
                {e.example_es && (
                  <span className="block text-xs text-muted">
                    <span lang="es">{e.example_es}</span> {e.example_en ? `· ${e.example_en}` : ''}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  )
}
