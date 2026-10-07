import type { EvalSummary, RateOut } from '../api/client'

const percent = (value: number | null | undefined) => (value == null ? '–' : `${Math.round(value * 100)}%`)

function RateTile({ label, rate, note }: { label: string; rate: RateOut; note: string }) {
  return (
    <div className="rounded-lg border border-line px-4 py-3">
      <p className="text-sm text-ink-2">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-ink">{percent(rate.value)}</p>
      <p className="mt-0.5 text-xs text-muted">
        {rate.n === 0
          ? 'No data yet'
          : `${rate.k} of ${rate.n} · 95% CI ${percent(rate.low)}–${percent(rate.high)}`}
      </p>
      <p className="mt-1 text-xs text-muted">{note}</p>
    </div>
  )
}

/** How the tutor is doing, measured from your own sessions (slice 4.4). Samples are small,
 *  so every number shows how many it rests on and its 95% interval. */
export function EvalPanel({ summary }: { summary: EvalSummary }) {
  return (
    <section aria-label="How the tutor is doing" className="rounded-xl border border-line bg-surface p-5">
      <h2 className="font-semibold text-ink">How the tutor is doing</h2>
      <p className="mt-1 text-sm text-ink-2">
        Measured from your own sessions. The samples are small, so each number shows what it
        rests on and its range.
      </p>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <RateTile
          label="Replies within your vocabulary"
          rate={summary.within_limit}
          note={`At most one new word per reply, the one the tutor teaches. First drafts: ${percent(summary.first_draft_within_limit.value)}.`}
        />
        <RateTile
          label="New words fully taught"
          rate={summary.complete}
          note="Replies that used a new word and taught every one of them."
        />
        <RateTile
          label="Words taught that were really new"
          rate={summary.new_word_precision}
          note="From your ratings on the Rate page: the rest you already knew."
        />
        <RateTile
          label="New words studied before finishing"
          rate={summary.studied_before_finishing}
          note="In the texts you finished: words studied before reading the end."
        />
      </div>
    </section>
  )
}
