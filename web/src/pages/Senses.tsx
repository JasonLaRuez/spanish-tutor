import { useEffect, useMemo, useState } from 'react'
import { api, type SenseEntry } from '../api/client'
import { RegisterTag } from '../components/Senses'
import { REGISTER_HINTS, REGISTERS, type Register } from '../lib/senses'
import { posName } from '../lib/text'

type Where = 'anywhere' | 'songs' | 'texts'

type Word = { lexeme_id: number; lemma: string; pos: string; main: string | null; songs: number; texts: number; recognized: boolean; senses: SenseEntry[] }

/** Strip accents and case, so "cancion" finds "canción". */
const fold = (text: string) => text.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase()

/** The senses the lexicon review kept apart from words' main definitions (slang, vulgar,
 * regional, rare, technical, archaic), by word, filterable by label and by where the word
 * appears. The index counts words, not senses: a song with the word may use its main sense. */
export function SensesPage() {
  const [entries, setEntries] = useState<SenseEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [shown, setShown] = useState<Set<Register>>(() => new Set(REGISTERS))
  const [where, setWhere] = useState<Where>('anywhere')
  const [knownOnly, setKnownOnly] = useState(false)
  const [search, setSearch] = useState('')

  useEffect(() => {
    api
      .senses()
      .then(setEntries)
      .catch((err) => setError(String(err)))
  }, [])

  const counts = useMemo(() => {
    const byRegister = new Map<Register, number>()
    for (const e of entries ?? []) byRegister.set(e.register, (byRegister.get(e.register) ?? 0) + 1)
    return byRegister
  }, [entries])

  const words = useMemo(() => {
    const query = fold(search.trim())
    const byWord = new Map<number, Word>()
    for (const e of entries ?? []) {
      if (!shown.has(e.register)) continue
      if (where === 'songs' && e.songs === 0) continue
      if (where === 'texts' && e.texts === 0) continue
      if (knownOnly && !e.recognized) continue
      if (query && !fold(e.lemma).includes(query) && !fold(e.sense_en).includes(query)) continue
      const word = byWord.get(e.lexeme_id) ?? {
        lexeme_id: e.lexeme_id, lemma: e.lemma, pos: e.pos, main: e.definition_en,
        songs: e.songs, texts: e.texts, recognized: e.recognized, senses: [],
      }
      word.senses.push(e)
      byWord.set(e.lexeme_id, word)
    }
    return [...byWord.values()]
  }, [entries, shown, where, knownOnly, search])

  const toggle = (register: Register) =>
    setShown((current) => {
      const next = new Set(current)
      if (next.has(register)) next.delete(register)
      else next.add(register)
      return next
    })

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-6 py-10 max-md:px-4">
      <div>
        <h1 className="text-2xl font-semibold text-ink">Slang &amp; other senses</h1>
        <p className="mt-1 max-w-prose text-ink-2">
          Meanings kept apart from a word’s main definition: slang, vulgar, regional, rare, technical
          and old-fashioned senses. Lessons show them under “Other senses”. Counts say where the{' '}
          <em>word</em> appears, not whether that text uses this sense.
        </p>
      </div>

      {error && <p className="text-danger">{error}</p>}
      {entries && entries.length === 0 && (
        <p className="text-muted">
          None yet. Senses are kept when a reviewed definition fix is applied (
          <code>evaluation lexicon-review apply</code>).
        </p>
      )}

      {entries && entries.length > 0 && (
        <>
          <div className="space-y-3">
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Labels">
              {REGISTERS.filter((r) => counts.has(r)).map((register) => (
                <button
                  key={register}
                  type="button"
                  aria-pressed={shown.has(register)}
                  title={REGISTER_HINTS[register]}
                  onClick={() => toggle(register)}
                  className={`rounded-full border px-2.5 py-0.5 text-sm ${
                    shown.has(register)
                      ? 'border-accent bg-accent-soft text-accent-text'
                      : 'border-line text-muted hover:text-ink'
                  }`}
                >
                  {register} <span className="tabular text-xs">{counts.get(register)}</span>
                </button>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-ink-2">
              <label className="flex items-center gap-2">
                Word appears
                <select
                  value={where}
                  onChange={(e) => setWhere(e.target.value as Where)}
                  className="rounded border border-line bg-surface px-1.5 py-0.5 text-ink"
                >
                  <option value="anywhere">anywhere</option>
                  <option value="songs">in my songs</option>
                  <option value="texts">in the library</option>
                </select>
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={knownOnly} onChange={(e) => setKnownOnly(e.target.checked)} />
                Only words I know
              </label>
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Find a word or meaning"
                aria-label="Find a word or meaning"
                className="w-56 max-w-full rounded border border-line bg-surface px-2 py-1 text-ink"
              />
            </div>
            <p className="text-sm text-muted tabular">
              {words.length} {words.length === 1 ? 'word' : 'words'}
            </p>
          </div>

          <ul className="grid gap-2 sm:grid-cols-2">
            {words.map((word) => (
              <li key={word.lexeme_id} className="min-w-0 rounded-lg border border-line bg-surface px-3 py-2.5">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span lang="es" className="es font-semibold text-ink">
                    {word.lemma}
                  </span>
                  <span className="text-xs text-muted">{posName(word.pos)}</span>
                  {word.recognized && <span className="text-xs text-muted">· known</span>}
                  <span className="ml-auto text-xs text-muted tabular">
                    {[
                      word.songs > 0 && `${word.songs} ${word.songs === 1 ? 'song' : 'songs'}`,
                      word.texts > 0 && `${word.texts} ${word.texts === 1 ? 'text' : 'texts'}`,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </span>
                </div>
                {word.main && <p className="mt-0.5 text-sm text-ink-2">{word.main}</p>}
                <ul className="mt-1.5 space-y-0.5">
                  {word.senses.map((sense) => (
                    <li key={sense.sense_id} className="flex items-baseline gap-1.5 text-sm text-ink">
                      <RegisterTag sense={sense} />
                      <span className="min-w-0">{sense.sense_en}</span>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
