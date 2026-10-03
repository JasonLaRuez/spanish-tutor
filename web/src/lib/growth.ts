import type { Growth } from '../api/client'
import { when } from './text'

export const MODES = ['recognition', 'production'] as const

export interface Point {
  label: string // "Seed", or the session's topic
  detail: string
  totals: Partial<Record<string, number>>
  added: Partial<Record<string, number>>
}

/** One point per session (the seed first), carrying both modes' running totals forward
 *  through sessions where one of them didn't change. */
export function toPoints(growth: Growth[]): Point[] {
  const points: Point[] = []
  const index = new Map<string, number>()
  const last: Partial<Record<string, number>> = {}
  for (const row of growth) {
    const key = String(row.session_id)
    if (!index.has(key)) {
      index.set(key, points.length)
      points.push({
        label: row.session_id === null ? 'Seed' : (row.topic ?? 'Open conversation'),
        detail: row.session_id === null ? 'Words marked in the seed list' : when(row.started_at),
        totals: {},
        added: {},
      })
    }
    const point = points[index.get(key)!]
    point.totals[row.mode] = row.running_total
    point.added[row.mode] = row.words_added
  }
  for (const point of points) {
    for (const mode of MODES) {
      if (point.totals[mode] === undefined) point.totals[mode] = last[mode]
      else last[mode] = point.totals[mode]
      if (point.added[mode] === undefined) point.added[mode] = 0
    }
  }
  return points
}
