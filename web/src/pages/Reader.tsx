import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, type ContentDetail, type NewWord } from '../api/client'
import { posName } from '../lib/text'

const FIRST_WORDS = 20

/** A plain reader for one song, story or chapter (until Phase 4's reading and lyrics
 *  skills): the words it would teach, most frequent first, then the text, and a button to
 *  mark it finished, which moves a book on to its next chapter. */
export function Reader() {
  const contentId = Number(useParams().contentId)
  const navigate = useNavigate()
  const [item, setItem] = useState<ContentDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api
      .content(contentId)
      .then(setItem)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [contentId])

  const finish = async () => {
    setBusy(true)
    try {
      await api.finishReading(contentId)
      navigate('/next')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(false)
    }
  }

  if (error) return <p className="p-8 text-danger">{error}</p>
  if (!item) return <p className="p-8 text-muted">Loading…</p>

  const where =
    item.kind === 'chapter'
      ? `${item.book_title} · chapter ${item.chapter_no} of ${item.chapters}`
      : item.kind === 'song'
        ? 'Song'
        : 'Story'
  const blocks =
    item.kind === 'song' ? [item.text_es] : item.text_es.split(/\n\s*\n/).filter((b) => b.trim())

  return (
    <div className="mx-auto max-w-3xl space-y-8 px-6 py-10">
      <div>
        <Link to="/next" className="text-sm text-accent-text hover:underline">
          ← What next?
        </Link>
        <h1 lang="es" className="mt-3 text-2xl font-semibold text-ink">{item.title}</h1>
        <p className="mt-1 text-sm text-muted">
          {where}
          {item.author ? ` · ${item.author}` : ''}
          {item.finished ? ' · finished' : ''}
        </p>
      </div>

      <NewWords words={item.new_words} />

      <article className="es space-y-4 text-[1.05rem] leading-relaxed text-ink">
        {blocks.map((block, i) => (
          <p key={i} className={item.kind === 'song' ? 'whitespace-pre-line' : undefined}>
            {item.kind === 'song' ? block : block.replace(/\s*\n\s*/g, ' ')}
          </p>
        ))}
      </article>

      <div className="flex flex-wrap items-center gap-3 border-t border-line pt-5">
        <button
          type="button"
          onClick={finish}
          disabled={busy}
          className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-ink disabled:opacity-50"
        >
          {item.finished ? 'Finished again' : 'Finished'}
        </button>
        <span className="text-sm text-ink-2">
          {item.kind === 'chapter' && item.chapter_no != null && item.chapter_no < item.chapters
            ? 'Your next suggestion will be the next chapter.'
            : 'Back to your suggestions.'}
        </span>
      </div>
    </div>
  )
}

function NewWords({ words }: { words: NewWord[] }) {
  const [all, setAll] = useState(false)
  if (words.length === 0) {
    return (
      <section className="rounded-xl border border-line bg-surface p-5">
        <h2 className="font-semibold text-ink">No new words</h2>
        <p className="mt-1 text-sm text-ink-2">You know every word in this one.</p>
      </section>
    )
  }
  const shown = all ? words : words.slice(0, FIRST_WORDS)
  return (
    <section aria-label="New words" className="space-y-3 rounded-xl border border-line bg-surface p-5">
      <div>
        <h2 className="font-semibold text-ink">
          {words.length.toLocaleString()} new {words.length === 1 ? 'word' : 'words'}
        </h2>
        <p className="text-sm text-ink-2">
          Most frequent here first. Reading doesn’t add them to your word bank yet: teaching them
          before you read comes with the reading skill.
        </p>
      </div>
      <ul className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
        {shown.map((word) => (
          <li key={word.lexeme_id} className="min-w-0 text-sm">
            <span className="es font-medium text-ink">{word.lemma}</span>{' '}
            <span className="text-xs text-muted">
              {posName(word.pos)} · ×{word.occurrences}
            </span>
            {word.model_written && (
              <span
                className="ml-1.5 rounded border border-line px-1 text-[0.65rem] uppercase tracking-wide text-muted"
                title="This word isn't in the dictionaries: Claude wrote its definition."
              >
                model-written
              </span>
            )}
            <span className="block truncate text-ink-2" title={word.definition_en ?? ''}>
              {word.definition_en ?? '(no definition)'}
            </span>
          </li>
        ))}
      </ul>
      {words.length > FIRST_WORDS && (
        <button
          type="button"
          onClick={() => setAll(!all)}
          className="text-sm text-accent-text hover:underline"
        >
          {all ? 'Show fewer' : `Show all ${words.length.toLocaleString()}`}
        </button>
      )}
    </section>
  )
}
