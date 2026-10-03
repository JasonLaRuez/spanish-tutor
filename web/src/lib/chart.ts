/** Round tick values for an axis: the first at or below `min`, the last at or above `max`. */
export function niceTicks(min: number, max: number, count = 4): number[] {
  const span = max - min || 1
  const raw = span / count
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? raw
  const start = Math.floor(min / step) * step
  const ticks: number[] = []
  let v = start
  do {
    ticks.push(Math.round(v * 1e6) / 1e6)
    v += step
  } while (ticks[ticks.length - 1] < max)
  return ticks
}
