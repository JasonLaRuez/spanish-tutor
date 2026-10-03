import type { Progress } from '../api/client'
import { formatNumber } from '../lib/text'

function Tile({ label, value, dot, note }: { label: string; value: number; dot?: string; note?: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface px-4 py-3">
      <p className="flex items-center gap-1.5 text-sm text-ink-2">
        {dot && <span className="h-2.5 w-2.5 rounded-full" style={{ background: dot }} aria-hidden />}
        {label}
      </p>
      <p className="mt-1 text-3xl font-semibold text-ink">{formatNumber(value)}</p>
      {note && <p className="mt-0.5 text-xs text-muted">{note}</p>}
    </div>
  )
}

/** The headline numbers: words recognized, words produced, and the gap between them. */
export function StatTiles({ progress }: { progress: Progress }) {
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      <Tile label="Words you recognize" value={progress.recognition} dot="var(--recognize)" />
      <Tile label="Words you can produce" value={progress.production} dot="var(--produce)" />
      <Tile
        label="Recognized, not yet used"
        value={Math.max(0, progress.recognition - progress.production)}
        note="The passive/active gap this tutor works on"
      />
    </div>
  )
}
