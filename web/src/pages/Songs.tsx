import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { api, type CatalogItem } from '../api/client'

const percent = (share: number) => `${Math.round(share * 100)}%`

/** The library of songs and poems: fewest new words first, each marked if it's over the
 *  difficulty ceiling. Opening one starts the lyrics skill (study, try, compare). */
export function Songs() {
  const navigate = useNavigate()
  const [items, setItems] = useState<CatalogItem[] | null>(null)
  const [ceiling, setCeiling] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api
      .catalog()
      .then((all) =>
        setItems(
          all
            .filter((item) => item.kind === 'song' || item.kind === 'poem')
            .sort((a, b) => (a.new_words ?? 1e9) - (b.new_words ?? 1e9) || a.title.localeCompare(b.title)),
        ),
      )
      .catch((err) => setError(String(err)))
    api
      .recommend()
      .then((recs) => setCeiling(recs.max_unknown_share))
      .catch(() => {})
  }, [])

  const open = async (contentId: number) => {
    setBusy(true)
    try {
      const started = await api.readingStart(contentId, 'requested')
      navigate(`/reading/${started.session_id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-6 py-10">
      <div>
        <h1 className="text-2xl font-semibold text-ink">Songs &amp; poems</h1>
        <p className="mt-1 text-ink-2">
          Translate a few lines yourself, then compare natural and literal translations. Fewest
          new words first.
        </p>
      </div>
      {error && <p className="text-danger">{error}</p>}
      {items && items.length === 0 && (
        <p className="text-muted">
          No songs or poems yet. Add your own with <code>content add-song</code>, or Bécquer’s
          Rimas with <code>ingest.gutenberg 53552</code> and <code>content add-poems</code>.
        </p>
      )}
      {items && items.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-line bg-surface">
          <table className="w-full text-sm">
            <thead className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-3 py-2.5 font-medium">Title</th>
                <th className="px-3 py-2.5 font-medium">By</th>
                <th className="px-3 py-2.5 text-right font-medium">New words</th>
                <th className="px-3 py-2.5 text-right font-medium">Words known</th>
                <th className="px-3 py-2.5 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="tabular">
              {items.map((item) => {
                const tooHard = ceiling != null && item.coverage != null && 1 - item.coverage > ceiling
                return (
                  <tr
                    key={item.content_id}
                    onClick={() => item.indexed && !busy && open(item.content_id)}
                    className={item.indexed ? 'cursor-pointer border-b border-line last:border-0 hover:bg-surface-2' : 'border-b border-line last:border-0 text-muted'}
                  >
                    <td lang="es" className="px-3 py-2 text-ink">
                      {item.title}
                      <span className="ml-2 text-xs text-muted">{item.kind === 'poem' ? 'poem' : 'song'}</span>
                    </td>
                    <td className="px-3 py-2 text-ink-2">{item.author ?? ''}</td>
                    <td className="px-3 py-2 text-right">{item.new_words == null ? 'not indexed' : item.new_words}</td>
                    <td className="px-3 py-2 text-right">
                      {item.coverage == null ? '' : percent(item.coverage)}
                      {tooHard && <span className="ml-1 text-xs text-muted">(too hard for now)</span>}
                    </td>
                    <td className="px-3 py-2 text-ink-2">
                      {item.state === 'finished' ? 'Finished' : item.state === 'started' ? 'Started' : ''}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
