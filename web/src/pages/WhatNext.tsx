import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import {
  api,
  type CatalogItem,
  type ChosenVia,
  type Recommendations,
  type RecommendedBook,
  type RecommendedItem,
} from '../api/client'

const percent = (share: number | null | undefined) =>
  share == null ? '–' : `${Math.round(share * 100)}%`

const count = (n: number, noun: string) => `${n.toLocaleString()} ${noun}${n === 1 ? '' : 's'}`

const KIND: Record<string, string> = { song: 'Song', story: 'Story', chapter: 'Chapter' }

/** "What next?": the recommender's two rankings, a surprise pick, and everything else to
 *  choose from. Starting an item logs how it was chosen (the default suggestion, or the
 *  learner's own pick) before the reader opens. */
export function WhatNext() {
  const navigate = useNavigate()
  const [recs, setRecs] = useState<Recommendations | null>(null)
  const [catalog, setCatalog] = useState<CatalogItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.recommend().then(setRecs).catch((err) => setError(String(err)))
    api.catalog().then(setCatalog).catch((err) => setError(String(err)))
  }, [])

  const open = async (contentId: number, chosenVia: ChosenVia) => {
    setBusy(true)
    try {
      await api.startReading(contentId, chosenVia)
      navigate(`/read/${contentId}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  const surprise = async () => {
    setBusy(true)
    try {
      const pick = await api.surprise()
      await open(pick.content_id, 'recommended')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  const empty = recs && catalog && catalog.length === 0
  const topItem = recs?.items[0]
  const topBook = recs?.books[0]
  // Surprise me only draws from what's within reach: easy songs and stories, or the next
  // chapter of a book in progress.
  const canSurprise = !!recs && (recs.items.length > 0 || recs.books.some((b) => b.state === 'in progress'))
  const known = recs ? percent(1 - recs.max_unknown_share) : ''

  return (
    <div className="mx-auto max-w-5xl space-y-8 px-6 py-10">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-ink">What next?</h1>
          <p className="mt-1 max-w-2xl text-ink-2">
            Ranked by how many words you’d need to learn first, so you start with what you can
            almost read already. Anything else is one click away below.
          </p>
        </div>
        {!empty && (
          <button
            type="button"
            onClick={surprise}
            disabled={busy || !canSurprise}
            title={canSurprise ? undefined : `Nothing is within reach yet: everything has more than ${percent(recs?.max_unknown_share)} new words.`}
            className="rounded-lg border border-line bg-surface px-4 py-2 text-sm font-medium text-ink hover:border-accent disabled:opacity-50"
          >
            Surprise me
          </button>
        )}
      </div>

      {error && <p className="text-danger">{error}</p>}
      {!recs && !error && <p className="text-muted">Loading…</p>}
      {empty && <EmptyState />}

      {recs && !empty && (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            {topItem ? (
              <SuggestionCard
                label="Song or story"
                title={topItem.title}
                detail={`${KIND[topItem.kind]} · ${count(topItem.new_words, 'new word')} · ${percent(topItem.coverage)} of its words known`}
                action="Start"
                disabled={busy}
                onStart={() => open(topItem.content_id, 'recommended')}
              />
            ) : (
              <NothingCard
                label="Song or story"
                text={
                  recs.too_hard_items.length
                    ? `None is within reach yet: each has more than ${percent(recs.max_unknown_share)} new words. See “Too hard for now” below.`
                    : 'No unfinished songs or stories.'
                }
              />
            )}
            {topBook ? (
              <SuggestionCard
                label="Book"
                title={topBook.title}
                detail={`${topBook.state === 'in progress' ? 'Next' : 'Begins with'}: chapter ${topBook.next_chapter_no}, ${topBook.next_chapter_title} · ${count(topBook.next_chapter_new_words, 'new word')} · ${percent(topBook.density)} of the book’s words new to you`}
                action={topBook.state === 'in progress' ? 'Continue' : 'Start'}
                disabled={busy}
                onStart={() => open(topBook.next_content_id, 'recommended')}
              />
            ) : (
              <NothingCard
                label="Book"
                text={
                  recs.too_hard_books.length
                    ? `None is within reach yet: each has more than ${percent(recs.max_unknown_share)} new words across the book. See “Too hard for now” below.`
                    : 'No unfinished books.'
                }
              />
            )}
          </div>

          {recs.items.length > 0 && (
            <ItemTable items={recs.items} disabled={busy} onOpen={open} />
          )}
          {recs.books.length > 0 && (
            <BookTable books={recs.books} disabled={busy} onOpen={open} />
          )}
          {(recs.too_hard_items.length > 0 || recs.too_hard_books.length > 0) && (
            <TooHard recs={recs} known={known} disabled={busy} onOpen={(id) => open(id, 'requested')} />
          )}
          {catalog && <Catalog items={catalog} disabled={busy} onOpen={(id) => open(id, 'requested')} />}
        </>
      )}
    </div>
  )
}

function SuggestionCard(props: {
  label: string
  title: string
  detail: string
  action: string
  disabled: boolean
  onStart: () => void
}) {
  return (
    <section
      aria-label={`Suggested ${props.label.toLowerCase()}`}
      className="flex min-w-0 flex-col gap-3 rounded-xl border border-accent bg-surface p-5"
    >
      <div className="min-w-0">
        <p className="text-xs font-medium uppercase tracking-wide text-accent-text">
          Suggested {props.label.toLowerCase()}
        </p>
        <h2 lang="es" className="mt-1 text-lg font-semibold text-ink">{props.title}</h2>
        <p className="mt-1 text-sm text-ink-2">{props.detail}</p>
      </div>
      <button
        type="button"
        onClick={props.onStart}
        disabled={props.disabled}
        className="self-start rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-ink disabled:opacity-50"
      >
        {props.action}
      </button>
    </section>
  )
}

function NothingCard({ label, text }: { label: string; text: string }) {
  return (
    <section className="rounded-xl border border-dashed border-line p-5">
      <p className="text-xs font-medium uppercase tracking-wide text-muted">{label}</p>
      <p className="mt-1 text-sm text-muted">{text}</p>
    </section>
  )
}

const th = 'px-3 py-2.5 font-medium'
const row = 'cursor-pointer border-b border-line last:border-0 hover:bg-surface-2'

function ItemTable(props: {
  items: RecommendedItem[]
  disabled: boolean
  onOpen: (id: number, via: ChosenVia) => void
}) {
  return (
    <section className="space-y-2">
      <h2 className="font-semibold text-ink">Songs and stories</h2>
      <p className="text-sm text-ink-2">Fewest new words first.</p>
      <div className="overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
            <tr>
              <th className={th}>Title</th>
              <th className={th}>Kind</th>
              <th className={`${th} text-right`}>New words</th>
              <th className={`${th} text-right`}>Words known</th>
            </tr>
          </thead>
          <tbody className="tabular">
            {props.items.map((item, i) => (
              <tr
                key={item.content_id}
                className={row}
                onClick={() => !props.disabled && props.onOpen(item.content_id, i === 0 ? 'recommended' : 'requested')}
              >
                <td lang="es" className="px-3 py-2.5 text-ink">{item.title}</td>
                <td className="px-3 py-2.5 text-ink-2">{KIND[item.kind]}</td>
                <td className="px-3 py-2.5 text-right">{item.new_words.toLocaleString()}</td>
                <td className="px-3 py-2.5 text-right">{percent(item.coverage)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function BookTable(props: {
  books: RecommendedBook[]
  disabled: boolean
  onOpen: (id: number, via: ChosenVia) => void
}) {
  return (
    <section className="space-y-2">
      <h2 className="font-semibold text-ink">Books</h2>
      <p className="text-sm text-ink-2">
        A book you’ve started comes first, at its next chapter. Others are ranked by the share of
        new words across the whole book.
      </p>
      <div className="overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
            <tr>
              <th className={th}>Book</th>
              <th className={th}>Next chapter</th>
              <th className={`${th} text-right`}>New in chapter</th>
              <th className={`${th} text-right`}>New in book</th>
            </tr>
          </thead>
          <tbody className="tabular">
            {props.books.map((book, i) => (
              <tr
                key={book.book_id}
                className={row}
                onClick={() =>
                  !props.disabled && props.onOpen(book.next_content_id, i === 0 ? 'recommended' : 'requested')
                }
              >
                <td className="px-3 py-2.5">
                  <span lang="es" className="text-ink">{book.title}</span>
                  {book.state === 'in progress' && (
                    <span className="ml-2 rounded bg-accent-soft px-1.5 py-0.5 text-xs text-accent-text">reading</span>
                  )}
                </td>
                <td className="px-3 py-2.5 text-ink-2">
                  <span className="text-muted">
                    Chapter {book.next_chapter_no} of {book.chapters} ·{' '}
                  </span>
                  <span lang="es">{book.next_chapter_title}</span>
                </td>
                <td className="px-3 py-2.5 text-right">{book.next_chapter_new_words.toLocaleString()}</td>
                <td className="px-3 py-2.5 text-right">
                  {book.new_words.toLocaleString()}{' '}
                  <span className="text-muted">({percent(book.density)})</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function TooHard(props: {
  recs: Recommendations
  known: string
  disabled: boolean
  onOpen: (id: number) => void
}) {
  const entries = [
    ...props.recs.too_hard_items.map((item) => ({
      key: `item-${item.content_id}`,
      id: item.content_id,
      title: item.title,
      from: KIND[item.kind],
      share: item.unknown_share,
      newWords: item.new_words,
    })),
    ...props.recs.too_hard_books.map((book) => ({
      key: `book-${book.book_id}`,
      id: book.next_content_id,
      title: book.title,
      from: `Book · next: chapter ${book.next_chapter_no}`,
      share: book.density ?? 1,
      newWords: book.new_words,
    })),
  ].sort((a, b) => a.share - b.share)
  return (
    <section aria-label="Too hard for now" className="space-y-2">
      <h2 className="font-semibold text-ink">Too hard for now</h2>
      <p className="text-sm text-ink-2">
        Never suggested until you know {props.known} of their words. You can still open one
        yourself.
      </p>
      <div className="overflow-x-auto rounded-lg border border-line bg-surface">
        <table className="w-full text-sm">
          <thead className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
            <tr>
              <th className={th}>Title</th>
              <th className={th}>Kind</th>
              <th className={`${th} text-right`}>Words known</th>
              <th className={`${th} text-right`}>New words</th>
            </tr>
          </thead>
          <tbody className="tabular">
            {entries.map((entry) => (
              <tr key={entry.key} className={row} onClick={() => !props.disabled && props.onOpen(entry.id)}>
                <td lang="es" className="px-3 py-2.5 text-ink">
                  {entry.title}
                </td>
                <td className="px-3 py-2.5 text-ink-2">{entry.from}</td>
                <td className="px-3 py-2.5 text-right">
                  {percent(1 - entry.share)} <span className="text-muted">(needs {props.known})</span>
                </td>
                <td className="px-3 py-2.5 text-right">{entry.newWords.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function Catalog(props: { items: CatalogItem[]; disabled: boolean; onOpen: (id: number) => void }) {
  return (
    <details className="rounded-lg border border-line bg-surface">
      <summary className="cursor-pointer px-4 py-3 font-semibold text-ink">
        Choose anything ({props.items.length})
        <span className="ml-2 text-sm font-normal text-ink-2">
          Any song, story or chapter, in any order. Your choice always wins.
        </span>
      </summary>
      <div className="overflow-x-auto border-t border-line">
        <table className="w-full text-sm">
          <thead className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
            <tr>
              <th className={th}>Title</th>
              <th className={th}>From</th>
              <th className={`${th} text-right`}>New words</th>
              <th className={th}>Status</th>
            </tr>
          </thead>
          <tbody className="tabular">
            {props.items.map((item) => (
              <tr
                key={item.content_id}
                className={item.indexed ? row : 'border-b border-line last:border-0 text-muted'}
                onClick={() => item.indexed && !props.disabled && props.onOpen(item.content_id)}
              >
                <td lang="es" className="px-3 py-2 text-ink">
                  {item.chapter_no != null ? `${item.chapter_no}. ` : ''}
                  {item.title}
                </td>
                <td lang="es" className="px-3 py-2 text-ink-2">{item.book_title ?? KIND[item.kind]}</td>
                <td className="px-3 py-2 text-right">
                  {item.new_words == null ? 'not indexed' : item.new_words.toLocaleString()}
                </td>
                <td className="px-3 py-2 text-ink-2">
                  {item.state === 'finished' ? 'Finished' : item.state === 'started' ? 'Started' : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

function EmptyState() {
  return (
    <section className="space-y-3 rounded-xl border border-dashed border-line p-6">
      <h2 className="font-semibold text-ink">Nothing to read yet</h2>
      <p className="text-sm text-ink-2">
        Add a song, a story or a book from the command line, then index it. Indexing records
        every word it uses, so it can be ranked against what you know.
      </p>
      <pre className="overflow-x-auto rounded-lg bg-surface-2 p-3 text-xs text-ink">
        {`uv run python -m spanish_tutor.ingest.gutenberg 13507
uv run python -m spanish_tutor.content add-book data/raw/gutenberg/13507 --title "..." --source gutenberg:13507
uv run python -m spanish_tutor.content add-song private/lyrics/song.txt --title "..."
uv run python -m spanish_tutor.content index`}
      </pre>
    </section>
  )
}
