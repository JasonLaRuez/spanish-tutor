import type { Readiness } from '../../api/client'
import { ChartCard, Legend, type Series } from './common'

const SERIES: Series[] = [
  { key: 'recognized', label: 'Recognize', color: 'var(--recognize)' },
  { key: 'produced', label: 'Can produce', color: 'var(--produce)' },
]

const SIZE = 96
const STROKE = 9
const OUTER = (SIZE - STROKE) / 2 // recognize
const INNER = OUTER - STROKE - 3 // can produce, inside it

const percent = (part: number, whole: number) => (whole ? (100 * part) / whole : 0)

function Arc({ radius, pct, color }: { radius: number; pct: number; color: string }) {
  const length = 2 * Math.PI * radius
  return (
    <>
      <circle cx={SIZE / 2} cy={SIZE / 2} r={radius} fill="none" stroke="var(--line)" strokeWidth={STROKE} />
      {pct > 0 && (
        <circle
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeDasharray={`${(pct / 100) * length} ${length}`}
          transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
        />
      )}
    </>
  )
}

/** One ring per CEFR level, cumulative: of the words textbooks introduce up to that level,
 * the share recognized (outer arc) and producible (inner arc). */
export function ReadinessRings({ levels }: { levels: Readiness[] }) {
  const caption = (
    <p className="mt-4 text-xs text-muted">
      Vocabulary only, not a CEFR level: the DELE and SIELE also test grammar, listening and
      writing. Levels: ELELex (CEFRLex, UCLouvain), the words graded textbooks use at each level.
    </p>
  )

  const chart = (
    <div>
      <div className="mb-4">
        <Legend series={SERIES} shape="rect" />
      </div>
      <ul className="grid grid-cols-3 gap-y-5 sm:grid-cols-5">
        {levels.map((level) => {
          const recognize = percent(level.recognized_up_to, level.words_up_to)
          const produce = percent(level.produced_up_to, level.words_up_to)
          return (
            <li key={level.level} className="flex flex-col items-center gap-1.5">
              <svg
                width={SIZE}
                height={SIZE}
                role="img"
                aria-label={`Up to ${level.level}: recognize ${Math.round(recognize)}%, can produce ${Math.round(produce)}%`}
              >
                <Arc radius={OUTER} pct={recognize} color="var(--recognize)" />
                <Arc radius={INNER} pct={produce} color="var(--produce)" />
                <text
                  x={SIZE / 2}
                  y={SIZE / 2 + 5}
                  textAnchor="middle"
                  fontSize={15}
                  fontWeight={600}
                  fill="var(--ink)"
                >
                  {level.level}
                </text>
              </svg>
              <p className="text-xs text-ink-2">up to {level.level}</p>
              <p className="tabular text-sm" aria-hidden>
                <span className="font-semibold text-ink">{Math.round(recognize)}%</span>
                <span className="text-muted"> · </span>
                <span className="text-ink-2">{Math.round(produce)}%</span>
              </p>
            </li>
          )
        })}
      </ul>
      {caption}
    </div>
  )

  const table = (
    <div>
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase tracking-wide text-muted">
          <tr>
            <th className="py-1.5 font-medium">Up to</th>
            <th className="py-1.5 text-right font-medium">Words</th>
            <th className="py-1.5 text-right font-medium">Recognize</th>
            <th className="py-1.5 text-right font-medium">Can produce</th>
            <th className="hidden py-1.5 text-right font-medium sm:table-cell">New at level</th>
          </tr>
        </thead>
        <tbody className="tabular">
          {levels.map((level) => (
            <tr key={level.level} className="border-t border-line">
              <td className="py-1.5 text-ink">{level.level}</td>
              <td className="py-1.5 text-right text-ink-2">{level.words_up_to.toLocaleString()}</td>
              <td className="py-1.5 text-right">
                {level.recognized_up_to.toLocaleString()} (
                {Math.round(percent(level.recognized_up_to, level.words_up_to))}%)
              </td>
              <td className="py-1.5 text-right">
                {level.produced_up_to.toLocaleString()} (
                {Math.round(percent(level.produced_up_to, level.words_up_to))}%)
              </td>
              <td className="hidden py-1.5 text-right text-ink-2 sm:table-cell">
                {level.words.toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {caption}
    </div>
  )

  if (levels.length === 0) {
    return (
      <section className="rounded-xl border border-line bg-surface p-5">
        <h2 className="font-semibold text-ink">Vocabulary readiness</h2>
        <p className="mt-1 text-sm text-ink-2">
          No words have levels yet. Run{' '}
          <code className="text-xs">uv run python -m spanish_tutor.ingest.elelex</code> to add them.
        </p>
      </section>
    )
  }

  return (
    <ChartCard
      title="Vocabulary readiness"
      subtitle="Of the words textbooks teach up to each level, how many you know"
      chart={chart}
      table={table}
    />
  )
}
