import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import { api, type CatalogItem } from '../api/client'

const percent = (share: number) => `${Math.round(share * 100)}%`
const byEase = (a: CatalogItem, b: CatalogItem) =>
  (a.new_words ?? 1e9) - (b.new_words ?? 1e9) || a.content_id - b.content_id

interface Group {
  name: string
  author: string | null
  items: CatalogItem[] // fewest new words first
}

/** Items of some kinds grouped by collection (Rimas, Platero y yo, an album), the group
 *  with the easiest item first. Opening an item starts a reading session. */
export function Library({
  kinds,
  title,
  intro,
  empty,
  loose,
  kindLabel,
}: {
  kinds: CatalogItem['kind'][]
  title: string
  intro: string
  empty: ReactNode
  loose: string // the group name for items without a collection
  kindLabel?: (item: CatalogItem) => string
}) {
  const navigate = useNavigate()
  const [items, setItems] = useState<CatalogItem[] | null>(null)
  const [ceiling, setCeiling] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [reachOnly, setReachOnly] = useState(false)

  useEffect(() => {
    api
      .catalog()
      .then((all) => setItems(all.filter((item) => kinds.includes(item.kind))))
      .catch((err) => setError(String(err)))
    api
      .recommend()
      .then((recs) => setCeiling(recs.max_unknown_share))
      .catch(() => {})
    // kinds is a constant per page
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const withinReach = (item: CatalogItem) =>
    ceiling == null || item.coverage == null || 1 - item.coverage <= ceiling

  const groups = useMemo(() => {
    const byName = new Map<string, Group>()
    for (const item of items ?? []) {
      const name = item.collection ?? loose
      const group = byName.get(name) ?? { name, author: item.author, items: [] }
      if (group.author !== item.author) group.author = null // several writers
      group.items.push(item)
      byName.set(name, group)
    }
    const list = [...byName.values()]
    for (const group of list) group.items.sort(byEase)
    return list.sort((a, b) => byEase(a.items[0], b.items[0]))
  }, [items, loose])

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
        <h1 className="text-2xl font-semibold text-ink">{title}</h1>
        <p className="mt-1 text-ink-2">{intro}</p>
      </div>
      {error && <p className="text-danger">{error}</p>}
      {items && items.length === 0 && <p className="text-muted">{empty}</p>}
      {items && items.length > 0 && (
        <>
          <label className="flex items-center gap-2 text-sm text-ink-2">
            <input type="checkbox" checked={reachOnly} onChange={(e) => setReachOnly(e.target.checked)} />
            Only what’s within reach{ceiling != null && ` (at most ${percent(ceiling)} new words)`}
          </label>
          <div className="space-y-3">
            {groups.map((group) => {
              const shown = reachOnly ? group.items.filter(withinReach) : group.items
              if (shown.length === 0) return null
              const reachable = group.items.filter(withinReach).length
              return (
                <details key={group.name} className="rounded-lg border border-line bg-surface" open={groups.length === 1}>
                  <summary className="flex cursor-pointer flex-wrap items-baseline gap-x-3 gap-y-1 px-4 py-3">
                    <span lang="es" className="font-medium text-ink">
                      {group.name}
                    </span>
                    {group.author && <span className="text-sm text-ink-2">{group.author}</span>}
                    <span className="ml-auto text-xs text-muted tabular">
                      {group.items.length} · {reachable} within reach
                    </span>
                  </summary>
                  <div className="overflow-x-auto border-t border-line">
                    <table className="w-full text-sm">
                      <thead className="text-left text-xs uppercase tracking-wide text-muted">
                        <tr>
                          <th className="px-3 py-2 font-medium">Title</th>
                          {group.author == null && <th className="px-3 py-2 font-medium max-sm:hidden">By</th>}
                          <th className="px-3 py-2 text-right font-medium">New words</th>
                          <th className="px-3 py-2 text-right font-medium">Words known</th>
                          <th className="px-3 py-2 font-medium max-sm:hidden">Status</th>
                        </tr>
                      </thead>
                      <tbody className="tabular">
                        {shown.map((item) => (
                          <tr
                            key={item.content_id}
                            onClick={() => item.indexed && !busy && open(item.content_id)}
                            className={
                              item.indexed
                                ? 'cursor-pointer border-t border-line hover:bg-surface-2'
                                : 'border-t border-line text-muted'
                            }
                          >
                            <td lang="es" className="px-3 py-2 text-ink">
                              {item.title}
                              {kindLabel && <span className="ml-2 text-xs text-muted">{kindLabel(item)}</span>}
                            </td>
                            {group.author == null && (
                              <td className="px-3 py-2 text-ink-2 max-sm:hidden">{item.author ?? ''}</td>
                            )}
                            <td className="px-3 py-2 text-right">
                              {item.new_words == null ? 'not indexed' : item.new_words}
                            </td>
                            <td className="px-3 py-2 text-right">
                              {item.coverage == null ? '' : percent(item.coverage)}
                              {!withinReach(item) && <span className="ml-1 text-xs text-muted">(too hard for now)</span>}
                            </td>
                            <td className="px-3 py-2 text-ink-2 max-sm:hidden">
                              {item.state === 'finished' ? 'Finished' : item.state === 'started' ? 'Started' : ''}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </details>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
