import { useState } from 'react'
import type { Growth } from '../../api/client'
import { niceTicks } from '../../lib/chart'
import { toPoints } from '../../lib/growth'
import { ChartCard, Legend, Tooltip, type Series } from './common'
import { useWidth } from '../../lib/useWidth'

const SERIES: Series[] = [
  { key: 'recognition', label: 'Recognize', color: 'var(--recognize)' },
  { key: 'production', label: 'Can produce', color: 'var(--produce)' },
]

const PAD = { top: 12, right: 112, bottom: 28, left: 48 }
const HEIGHT = 240

/** Word bank size after each session: a line per mode, with a crosshair readout. */
export function GrowthChart({ growth }: { growth: Growth[] }) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const points = toPoints(growth)
  const values = points.flatMap((p) => SERIES.map((s) => p.totals[s.key] ?? 0))
  const ticks = niceTicks(Math.min(...values) * 0.95, Math.max(...values), 4)
  const [lo, hi] = [ticks[0], ticks[ticks.length - 1]]
  const plotW = Math.max(80, width - PAD.left - PAD.right)
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const x = (i: number) => PAD.left + (points.length > 1 ? (i / (points.length - 1)) * plotW : plotW / 2)
  const y = (v: number) => PAD.top + plotH - ((v - lo) / (hi - lo || 1)) * plotH

  const nearest = (clientX: number, rect: DOMRect) => {
    const px = clientX - rect.left
    let best = 0
    points.forEach((_, i) => {
      if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i
    })
    return best
  }

  const chart = (
    <div ref={ref} className="relative">
      <div className="mb-3">
        <Legend series={SERIES} shape="line" />
      </div>
      <svg
        width={width}
        height={HEIGHT}
        role="img"
        aria-label="Words in your word bank after each session"
        onPointerMove={(event) => setHover(nearest(event.clientX, event.currentTarget.getBoundingClientRect()))}
        onPointerLeave={() => setHover(null)}
      >
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={PAD.left} x2={PAD.left + plotW} y1={y(tick)} y2={y(tick)} stroke="var(--line)" strokeWidth={1} />
            <text x={PAD.left - 8} y={y(tick) + 4} textAnchor="end" fontSize={11} fill="var(--muted)" className="tabular">
              {tick.toLocaleString()}
            </text>
          </g>
        ))}
        {points.map((point, i) => (
          <text key={i} x={x(i)} y={HEIGHT - 8} textAnchor="middle" fontSize={11} fill="var(--muted)">
            {point.label.length > 14 ? point.label.slice(0, 13) + '…' : point.label}
          </text>
        ))}
        {hover !== null && (
          <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + plotH} stroke="var(--axis)" strokeWidth={1} />
        )}
        {SERIES.map((s) => {
          const coords = points.map((p, i) => [x(i), y(p.totals[s.key] ?? lo)] as const)
          const lastPoint = coords[coords.length - 1]
          return (
            <g key={s.key}>
              <polyline
                points={coords.map(([px, py]) => `${px},${py}`).join(' ')}
                fill="none"
                stroke={s.color}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
              {coords.map(([px, py], i) => (
                <circle key={i} cx={px} cy={py} r={4} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
              ))}
              {lastPoint && (
                <text x={lastPoint[0] + 10} y={lastPoint[1] + 4} fontSize={12} fill="var(--ink-2)" className="tabular">
                  {(points[points.length - 1].totals[s.key] ?? 0).toLocaleString()} {s.label.toLowerCase()}
                </text>
              )}
            </g>
          )
        })}
      </svg>
      {hover !== null && (
        <Tooltip
          x={x(hover)}
          y={PAD.top + 48}
          title={`${points[hover].label} · ${points[hover].detail}`}
          rows={SERIES.map((s) => ({
            color: s.color,
            label: s.label,
            value: `${(points[hover].totals[s.key] ?? 0).toLocaleString()} (+${points[hover].added[s.key] ?? 0})`,
          }))}
        />
      )}
    </div>
  )

  const table = (
    <table className="w-full text-sm">
      <thead className="text-left text-xs uppercase tracking-wide text-muted">
        <tr>
          <th className="py-1.5 font-medium">Session</th>
          <th className="py-1.5 text-right font-medium">Recognize</th>
          <th className="py-1.5 text-right font-medium">Can produce</th>
        </tr>
      </thead>
      <tbody className="tabular">
        {points.map((point, i) => (
          <tr key={i} className="border-t border-line">
            <td className="py-1.5 text-ink">
              {point.label} <span className="text-muted">{point.detail}</span>
            </td>
            {SERIES.map((s) => (
              <td key={s.key} className="py-1.5 text-right">
                {(point.totals[s.key] ?? 0).toLocaleString()}{' '}
                <span className="text-muted">(+{point.added[s.key] ?? 0})</span>
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )

  return (
    <ChartCard
      title="Word bank growth"
      subtitle="Words you know after each conversation"
      chart={chart}
      table={table}
    />
  )
}
