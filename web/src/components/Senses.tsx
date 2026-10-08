import { REGISTER_HINTS, type Sense } from '../lib/senses'

/** A sense's label: its register, with the region for a regional sense. */
export function RegisterTag({ sense }: { sense: Sense }) {
  const vulgar = sense.register === 'vulgar'
  return (
    <span
      className={`shrink-0 rounded border px-1 text-[0.65rem] uppercase tracking-wide ${
        vulgar ? 'border-danger text-danger' : 'border-line text-muted'
      }`}
      title={REGISTER_HINTS[sense.register]}
    >
      {vulgar && <span aria-hidden="true">⚠ </span>}
      {sense.register}
      {sense.region ? ` · ${sense.region}` : ''}
    </span>
  )
}

/** A word's other senses (rare, regional, slang, vulgar…), kept apart from its main
 * definition by the lexicon review, each labeled. */
export function OtherSenses({ senses }: { senses: Sense[] }) {
  if (senses.length === 0) return null
  return (
    <div className="mt-1.5">
      <p className="text-[0.65rem] uppercase tracking-wide text-muted">Other senses</p>
      <ul className="mt-0.5 space-y-0.5">
        {senses.map((sense, i) => (
          <li key={i} className="flex items-baseline gap-1.5 text-xs text-ink-2">
            <RegisterTag sense={sense} />
            <span className="min-w-0">{sense.sense_en}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
