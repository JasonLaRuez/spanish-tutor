import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type EvalOverview, type RateOut, type RatingItem, type RatingQueue } from '../api/client'
import { SpeakButton } from '../components/SpeakButton'
import { posName } from '../lib/text'

/** What each kind of item is called on the page. */
const KINDS: { type: string; name: string }[] = [
  { type: 'new_word_flag', name: 'New words' },
  { type: 'translation_line', name: 'Translations' },
]

/** How each label reads as a button. */
const LABELS: Record<string, string> = {
  new: 'New to me',
  known: 'I knew it',
  not_a_word: 'Not a real word',
}

const percent = (value: number | null | undefined) => (value == null ? '–' : `${Math.round(value * 100)}%`)

function Precision({ name, rate }: { name: string; rate: RateOut }) {
  return (
    <div>
      <dt className="text-xs text-muted">{name}</dt>
      <dd className="text-xl font-semibold text-ink">{percent(rate.value)}</dd>
      <dd className="text-xs text-muted">
        {rate.n === 0 ? 'nothing rated yet' : `${rate.k} of ${rate.n} · 95% CI ${percent(rate.low)}–${percent(rate.high)}`}
      </dd>
    </div>
  )
}

/** The evaluation's hand ratings (slice 4.4): the items the judges and metrics are checked
 *  against, one at a time, with the choices for each kind of item. */
export function RatePage() {
  const [kind, setKind] = useState('new_word_flag')
  const [queue, setQueue] = useState<RatingQueue | null>(null)
  const [overview, setOverview] = useState<EvalOverview | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    api.ratingQueue(kind).then(setQueue).catch((e) => setError(String(e)))
    api.evalOverview().then(setOverview).catch(() => {})
  }, [kind])

  useEffect(load, [load])

  const rate = useCallback(
    async (item: RatingItem, rating: { label?: string; score?: number }) => {
      try {
        await api.rate(item.item_id, rating)
        setError(null)
        load()
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    },
    [load],
  )

  const pending = queue?.items.filter((item) => item.label == null && item.score == null) ?? []
  const done = queue?.items.filter((item) => item.label != null || item.score != null) ?? []
  const current = pending[0]
  const choices = useMemo(() => (queue ? choicesFor(queue) : []), [queue])

  // Keys 1, 2, 3... rate the item on screen.
  useEffect(() => {
    if (!current) return
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return
      const choice = choices[Number(event.key) - 1]
      if (choice) rate(current, choice.rating)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [current, choices, rate])

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="text-2xl font-semibold text-ink">Rate</h1>
      <p className="mt-1 text-sm text-ink-2">
        Your judgments, which the evaluation checks the tutor and its judges against.
      </p>

      {overview && (
        <dl className="mt-6 grid grid-cols-3 gap-4" aria-label="New-word precision">
          <Precision name="Taught words really new" rate={overview.new_word_precision.all} />
          <Precision name="… in your conversations" rate={overview.new_word_precision.real} />
          <Precision name="… in the benchmark" rate={overview.new_word_precision.benchmark} />
        </dl>
      )}

      <div role="tablist" className="mt-8 flex gap-1 border-b border-line">
        {KINDS.map(({ type, name }) => {
          const counts = overview?.progress[type]
          return (
            <button
              key={type}
              role="tab"
              aria-selected={kind === type}
              onClick={() => setKind(type)}
              className={`-mb-px border-b-2 px-3 py-2 text-sm ${
                kind === type ? 'border-accent font-medium text-ink' : 'border-transparent text-ink-2 hover:text-ink'
              }`}
            >
              {name}
              {counts && <span className="ml-1 text-xs text-muted"> {counts.rated}/{counts.items}</span>}
            </button>
          )
        })}
      </div>

      {error && <p className="mt-4 text-sm text-danger">{error}</p>}

      {queue && (
        <section aria-label="To rate" className="mt-6">
          {current ? (
            <div className="rounded-xl border border-line bg-surface p-5">
              <p className="text-sm font-medium text-ink-2">{queue.criterion.question}</p>
              <ItemView item={current} />
              <div className="mt-4 flex flex-wrap gap-2">
                {choices.map((choice, i) => (
                  <button
                    key={choice.text}
                    type="button"
                    onClick={() => rate(current, choice.rating)}
                    aria-label={choice.text}
                    aria-keyshortcuts={String(i + 1)}
                    className="rounded-lg border border-line px-3 py-1.5 text-sm text-ink hover:bg-surface-2"
                  >
                    <span aria-hidden className="mr-1.5 text-xs text-muted">{i + 1}</span>
                    {choice.text}
                  </button>
                ))}
              </div>
              <p className="mt-3 text-xs text-muted">{pending.length} left to rate</p>
            </div>
          ) : (
            <p className="text-ink-2">
              {queue.items.length ? 'Everything here is rated. You can change a rating below.' : 'Nothing to rate yet.'}
            </p>
          )}
        </section>
      )}

      {done.length > 0 && queue && (
        <section aria-label="Rated" className="mt-8">
          <h2 className="text-sm font-medium text-ink">Rated</h2>
          <ul className="mt-2 divide-y divide-line">
            {done.map((item) => (
              <li key={item.item_id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                <span className="es min-w-0 flex-1 text-ink">{summary(item)}</span>
                {choices.map((choice) => {
                  const chosen = choice.rating.label != null ? item.label === choice.rating.label : item.score === choice.rating.score
                  return (
                    <button
                      key={choice.text}
                      type="button"
                      aria-pressed={chosen}
                      onClick={() => !chosen && rate(item, choice.rating)}
                      className={`rounded px-2 py-0.5 text-xs ${
                        chosen ? 'bg-accent-soft font-medium text-ink' : 'text-muted hover:text-ink'
                      }`}
                    >
                      {choice.text}
                    </button>
                  )
                })}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

function choicesFor(queue: RatingQueue): { text: string; rating: { label?: string; score?: number } }[] {
  const { labels, scale } = queue.criterion
  if (scale) {
    const [low, high] = scale
    return Array.from({ length: high - low + 1 }, (_, i) => ({ text: String(low + i), rating: { score: low + i } }))
  }
  return labels.map((label) => ({ text: LABELS[label] ?? label, rating: { label } }))
}

/** One line for the rated list. */
function summary(item: RatingItem): string {
  const content = item.content as Record<string, string>
  return content.lemma ?? content.natural_en ?? item.source_ref
}

/** What the rater sees: for a taught word, the word, its meaning and the sentence it was
 *  taught in; for anything else, the snapshot's fields. */
function ItemView({ item }: { item: RatingItem }) {
  const content = item.content as Record<string, string | null>
  const where = item.source_ref.startsWith('benchmark:') ? 'Benchmark' : 'Your conversation'
  if (item.item_type === 'new_word_flag') {
    return (
      <div className="mt-3 space-y-2">
        <div className="flex items-baseline gap-2">
          <span className="es text-2xl font-semibold text-ink">{content.lemma}</span>
          {content.lemma && <SpeakButton text={content.lemma} label={`Listen: ${content.lemma}`} />}
          {content.pos && <span className="text-sm text-muted">{posName(content.pos)}</span>}
        </div>
        <p className="text-sm text-ink-2">{content.definition_en ?? '(no definition)'}</p>
        <p className="es border-l-2 border-line pl-3 text-ink">{content.sentence}</p>
        <p className="text-xs text-muted">
          {where}
          {content.topic ? ` · ${content.topic}` : ''}
        </p>
      </div>
    )
  }
  return (
    <dl className="mt-3 space-y-1 text-sm">
      {Object.entries(content).map(([key, value]) => (
        <div key={key}>
          <dt className="text-xs text-muted">{key}</dt>
          <dd className="text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  )
}
