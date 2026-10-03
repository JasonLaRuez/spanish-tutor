import { useState, type ReactNode } from 'react'

export interface Series {
  key: string
  label: string
  color: string // a CSS color, e.g. var(--recognize)
}

/** Legend: rect keys for bars, line keys for lines. Text stays in ink, never the series color. */
export function Legend({ series, shape }: { series: Series[]; shape: 'rect' | 'line' }) {
  return (
    <ul className="flex flex-wrap gap-4 text-sm text-ink-2">
      {series.map((s) => (
        <li key={s.key} className="flex items-center gap-1.5">
          {shape === 'rect' ? (
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: s.color }} aria-hidden />
          ) : (
            <span className="h-0.5 w-4 rounded-full" style={{ background: s.color }} aria-hidden />
          )}
          {s.label}
        </li>
      ))}
    </ul>
  )
}

/** A tooltip positioned inside the chart's relative container. Values lead, labels follow. */
export function Tooltip({
  x,
  y,
  title,
  rows,
}: {
  x: number
  y: number
  title: string
  rows: { color: string; label: string; value: string }[]
}) {
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-10 min-w-36 -translate-x-1/2 -translate-y-full rounded-md border border-line bg-surface px-2.5 py-2 text-xs shadow-md"
      style={{ left: x, top: y - 8 }}
    >
      <p className="mb-1 text-muted">{title}</p>
      {rows.map((row) => (
        <p key={row.label} className="flex items-center gap-2">
          <span className="h-0.5 w-3 rounded-full" style={{ background: row.color }} aria-hidden />
          <span className="tabular font-semibold text-ink">{row.value}</span>
          <span className="text-ink-2">{row.label}</span>
        </p>
      ))}
    </div>
  )
}

/** A card holding one chart, with a chart/table switch so no value hides behind hover. */
export function ChartCard({
  title,
  subtitle,
  chart,
  table,
}: {
  title: string
  subtitle?: string
  chart: ReactNode
  table: ReactNode
}) {
  const [view, setView] = useState<'chart' | 'table'>('chart')
  return (
    <section className="rounded-xl border border-line bg-surface p-5">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="font-semibold text-ink">{title}</h2>
          {subtitle && <p className="text-sm text-ink-2">{subtitle}</p>}
        </div>
        <div className="flex rounded-md border border-line text-xs" role="group" aria-label="View">
          {(['chart', 'table'] as const).map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setView(option)}
              aria-pressed={view === option}
              className={`px-2.5 py-1 capitalize ${
                view === option ? 'bg-surface-2 font-medium text-ink' : 'text-ink-2 hover:text-ink'
              }`}
            >
              {option}
            </button>
          ))}
        </div>
      </div>
      {view === 'chart' ? chart : table}
    </section>
  )
}
