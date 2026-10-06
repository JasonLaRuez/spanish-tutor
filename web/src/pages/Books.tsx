import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { api, type CatalogItem, type Recommendations, type RecommendedBook } from '../api/client'

interface Book {
  id: number
  title: string
  author: string | null
  chapters: CatalogItem[]
}

const percent = (share: number) => `${Math.round(share * 100)}%`

/** The library of books: progress through each, its share of new words, and its next
 *  chapter. Any chapter can be opened, in any order: an explicit choice always wins. */
export function Books() {
  const navigate = useNavigate()
  const [books, setBooks] = useState<Book[] | null>(null)
  const [recs, setRecs] = useState<Recommendations | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api
      .catalog()
      .then((items) => {
        const byBook = new Map<number, Book>()
        for (const item of items) {
          if (item.book_id == null) continue
          const book = byBook.get(item.book_id) ?? {
            id: item.book_id,
            title: item.book_title ?? '',
            author: item.author,
            chapters: [],
          }
          book.chapters.push(item)
          byBook.set(item.book_id, book)
        }
        setBooks([...byBook.values()])
      })
      .catch((err) => setError(String(err)))
    api.recommend().then(setRecs).catch(() => {})
  }, [])

  const read = async (contentId: number) => {
    setBusy(true)
    try {
      const started = await api.readingStart(contentId, 'requested')
      navigate(`/reading/${started.session_id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  const ranked = (id: number): RecommendedBook | undefined =>
    [...(recs?.books ?? []), ...(recs?.too_hard_books ?? [])].find((b) => b.book_id === id)

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-6 py-10">
      <div>
        <h1 className="text-2xl font-semibold text-ink">Books</h1>
        <p className="mt-1 text-ink-2">Your books, chapter by chapter, in order.</p>
      </div>
      {error && <p className="text-danger">{error}</p>}
      {books && books.length === 0 && (
        <p className="text-muted">No books yet. Add one from the command line (see “What next?”).</p>
      )}
      {books?.map((book) => {
        const done = book.chapters.filter((c) => c.state === 'finished').length
        const info = ranked(book.id)
        const tooHard = !!recs?.too_hard_books.some((b) => b.book_id === book.id)
        return (
          <section key={book.id} aria-label={book.title} className="space-y-3 rounded-xl border border-line bg-surface p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 lang="es" className="text-lg font-semibold text-ink">
                  {book.title}
                </h2>
                <p className="text-sm text-muted">
                  {book.author ? `${book.author} · ` : ''}
                  {done} of {book.chapters.length} chapters read
                  {info?.density != null ? ` · ${percent(info.density)} of its words new to you` : ''}
                  {tooHard ? ' · too hard for now' : ''}
                </p>
              </div>
              {info ? (
                <button
                  type="button"
                  onClick={() => read(info.next_content_id)}
                  disabled={busy}
                  className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-ink disabled:opacity-50"
                >
                  {info.state === 'in progress' ? 'Continue' : 'Start'}: chapter {info.next_chapter_no}
                </button>
              ) : (
                done === book.chapters.length && <span className="text-sm text-muted">Finished</span>
              )}
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden>
              <div className="h-full rounded-full bg-accent" style={{ width: `${(done / book.chapters.length) * 100}%` }} />
            </div>
            <details>
              <summary className="cursor-pointer text-sm text-accent-text">Chapters</summary>
              <ol className="mt-2 divide-y divide-line text-sm">
                {book.chapters.map((chapter) => (
                  <li key={chapter.content_id} className="flex items-center justify-between gap-3 py-1.5">
                    <button
                      type="button"
                      lang="es"
                      onClick={() => read(chapter.content_id)}
                      disabled={busy || !chapter.indexed}
                      className="min-w-0 truncate text-left text-ink hover:text-accent-text disabled:text-muted"
                    >
                      {chapter.chapter_no}. {chapter.title}
                    </button>
                    <span className="shrink-0 text-xs text-muted">
                      {chapter.state === 'finished' ? 'read · ' : chapter.state === 'started' ? 'started · ' : ''}
                      {chapter.new_words == null ? 'not indexed' : `${chapter.new_words.toLocaleString()} new words`}
                    </span>
                  </li>
                ))}
              </ol>
            </details>
          </section>
        )
      })}
    </div>
  )
}
