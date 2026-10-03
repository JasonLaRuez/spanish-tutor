import { useState } from 'react'
import type { Band } from '../../api/client'
import { ChartCard, Legend, Tooltip, type Series } from './common'
import { useWidth } from '../../lib/useWidth'

const SERIES: Series[] = [
  { key: 'recognized', label: 'Recognize', color: 'var(--recognize)' },
  { key: 'produced', label: 'Can produce', color: 'var(--produce)' },
]

const BAR = 12 // px per bar; two bars per band
const GAP = 2 // surface gap between a band's two bars
const ROW = BAR * 2 + GAP + 18 // bars + air between bands
const LABEL_W = 96
const VALUE_W = 44

const percent = (part: number, whole: number) => (whole ? (100 * part) / whole : 0)

/** Coverage of each frequency band: grouped horizontal bars, recognize vs. can produce. */
export function BandChart({ bands }: { bands: Band[] }) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<{ band: Band; series: Series; x: number; y: number } | null>(
    null,
  )
  const plot = Math.max(120, width - LABEL_W - VALUE_W)
  const x = (pct: number) => LABEL_W + (pct / 100) * plot
  const height = bands.length * ROW + 24

  const chart = (
    <div ref={ref} className="relative">
      <div className="mb-3">
        <Legend series={SERIES} shape="rect" />
      </div>
      <svg width={width} height={height} role="img" aria-label="Coverage by frequency band">
        {[0, 25, 50, 75, 100].map((tick) => (
          <g key={tick}>
            <line x1={x(tick)} x2={x(tick)} y1={0} y2={height - 20} stroke="var(--line)" strokeWidth={1} />
            <text x={x(tick)} y={height - 4} textAnchor="middle" fontSize={11} fill="var(--muted)" className="tabular">
              {tick}%
            </text>
          </g>
        ))}
        <line x1={x(0)} x2={x(0)} y1={0} y2={height - 20} stroke="var(--axis)" strokeWidth={1} />
        {bands.map((band, i) => {
          const top = i * ROW + 6
          return (
            <g key={band.band}>
              <text x={LABEL_W - 10} y={top + BAR + 4} textAnchor="end" fontSize={12} fill="var(--ink-2)">
                {band.label}
              </text>
              {SERIES.map((series, j) => {
                const value = series.key === 'recognized' ? band.recognized : band.produced
                const pct = percent(value, band.words)
                const y = top + j * (BAR + GAP)
                const w = Math.max(0, x(pct) - x(0))
                const r = Math.min(4, w / 2)
                // Square at the baseline, 4px rounded at the data end.
                const path = `M${x(0)},${y} h${w - r} a${r},${r} 0 0 1 ${r},${r} v${BAR - 2 * r} a${r},${r} 0 0 1 ${-r},${r} h${-(w - r)} z`
                const active = hover?.band.band === band.band && hover.series.key === series.key
                return (
                  <g
                    key={series.key}
                    tabIndex={0}
                    onPointerEnter={() => setHover({ band, series, x: x(pct), y })}
                    onPointerLeave={() => setHover(null)}
                    onFocus={() => setHover({ band, series, x: x(pct), y })}
                    onBlur={() => setHover(null)}
                    className="outline-none"
                  >
                    {/* The hit area is the whole row slot, larger than the bar. */}
                    <rect x={x(0)} y={y - 1} width={plot} height={BAR + GAP} fill="transparent" />
                    {w > 0 && (
                      <path d={path} fill={series.color} opacity={hover && !active ? 0.55 : 1} />
                    )}
                    <text x={x(pct) + 6} y={y + BAR - 2} fontSize={11} fill="var(--ink-2)" className="tabular">
                      {Math.round(pct)}%
                    </text>
                  </g>
                )
              })}
            </g>
          )
        })}
      </svg>
      {hover && (
        <Tooltip
          x={hover.x}
          y={hover.y + 40}
          title={`Words ranked ${hover.band.label}`}
          rows={[
            {
              color: hover.series.color,
              label: hover.series.label,
              value: `${Math.round(percent(hover.series.key === 'recognized' ? hover.band.recognized : hover.band.produced, hover.band.words))}% (${(hover.series.key === 'recognized' ? hover.band.recognized : hover.band.produced).toLocaleString()} of ${hover.band.words.toLocaleString()})`,
            },
          ]}
        />
      )}
    </div>
  )

  const table = (
    <table className="w-full text-sm">
      <thead className="text-left text-xs uppercase tracking-wide text-muted">
        <tr>
          <th className="py-1.5 font-medium">Frequency rank</th>
          <th className="py-1.5 text-right font-medium">Words</th>
          <th className="py-1.5 text-right font-medium">Recognize</th>
          <th className="py-1.5 text-right font-medium">Can produce</th>
        </tr>
      </thead>
      <tbody className="tabular">
        {bands.map((band) => (
          <tr key={band.band} className="border-t border-line">
            <td className="py-1.5 text-ink">{band.label}</td>
            <td className="py-1.5 text-right text-ink-2">{band.words.toLocaleString()}</td>
            <td className="py-1.5 text-right">
              {band.recognized.toLocaleString()} ({Math.round(percent(band.recognized, band.words))}%)
            </td>
            <td className="py-1.5 text-right">
              {band.produced.toLocaleString()} ({Math.round(percent(band.produced, band.words))}%)
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )

  return (
    <ChartCard
      title="Coverage by word frequency"
      subtitle="The share of Spanish's most common words you know, band by band"
      chart={chart}
      table={table}
    />
  )
}
