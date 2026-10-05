import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, type SessionSummary, type Transcript } from '../api/client'
import { LearnerMessage, Note } from '../components/Messages'
import { SummaryCard } from '../components/SummaryCard'
import { when } from '../lib/text'

export function HistoryList() {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    api.sessions().then(setSessions).catch((err) => setError(String(err)))
  }, [])

  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      <h1 className="text-2xl font-semibold text-ink">History</h1>
      <p className="mt-1 text-ink-2">Every conversation, with what you said and learned.</p>
      {error && <p className="mt-6 text-danger">{error}</p>}
      {sessions && sessions.length === 0 && <p className="mt-6 text-muted">No conversations yet.</p>}
      {sessions && sessions.length > 0 && (
        <div className="mt-6 overflow-x-auto rounded-lg border border-line bg-surface">
          <table className="w-full text-sm">
            <thead className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-4 py-2.5 font-medium">Topic</th>
                <th className="px-4 py-2.5 font-medium">Started</th>
                <th className="px-3 py-2.5 text-right font-medium">Messages</th>
                <th className="px-3 py-2.5 text-right font-medium">¿Cómo se dice?</th>
                <th className="px-3 py-2.5 text-right font-medium">Corrections</th>
                <th className="px-3 py-2.5 text-right font-medium">Words taught</th>
                <th className="px-3 py-2.5 text-right font-medium">Words used</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="tabular">
              {sessions.map((s) => (
                <tr
                  key={s.session_id}
                  onClick={() => navigate(`/history/${s.session_id}`)}
                  className="cursor-pointer border-b border-line last:border-0 hover:bg-surface-2"
                >
                  <td className="px-4 py-2.5 text-ink">{s.topic ?? 'Open conversation'}</td>
                  <td className="px-4 py-2.5 text-ink-2">{when(s.started_at)}</td>
                  <td className="px-3 py-2.5 text-right">{s.messages}</td>
                  <td className="px-3 py-2.5 text-right">{s.how_to_say}</td>
                  <td className="px-3 py-2.5 text-right">{s.corrections}</td>
                  <td className="px-3 py-2.5 text-right">{s.words_taught}</td>
                  <td className="px-3 py-2.5 text-right">{s.words_used}</td>
                  <td className="px-4 py-2.5 text-right">
                    {s.ended_at && <span className="text-muted">Ended</span>}
                    {!s.ended_at && !s.active && <span className="text-muted">Left open</span>}
                    {s.active && (
                      <Link
                        to={`/chat/${s.session_id}`}
                        onClick={(event) => event.stopPropagation()}
                        className="text-accent-text hover:underline"
                      >
                        Continue
                      </Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? '' : 's'}`

function Words({ label, words }: { label: string; words: string[] }) {
  if (!words.length) return null
  return (
    <p className="text-xs text-muted">
      {label}: {words.join(', ')}
    </p>
  )
}

export function TranscriptPage() {
  const sessionId = Number(useParams().sessionId)
  const [transcript, setTranscript] = useState<Transcript | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .transcript(sessionId)
      .then(setTranscript)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [sessionId])

  if (error) return <p className="p-8 text-danger">{error}</p>
  if (!transcript) return <p className="p-8 text-muted">Loading…</p>
  const { session, turns, summary } = transcript

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <Link to="/history" className="text-sm text-accent-text hover:underline">
        ← History
      </Link>
      <div className="mt-3 flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-2xl font-semibold text-ink">{session.topic ?? 'Open conversation'}</h1>
        {session.active && (
          <Link to={`/chat/${session.session_id}`} className="text-accent-text hover:underline">
            Continue this conversation →
          </Link>
        )}
      </div>
      <p className="mt-1 text-sm text-muted">
        {when(session.started_at)} · {count(session.messages, 'message')} ·{' '}
        {count(session.words_taught, 'word')} taught · {count(session.words_used, 'word')} used
      </p>

      <div className="mt-8 space-y-5">
        {turns.map((turn) =>
          turn.role === 'learner' ? (
            <div key={turn.turn_no} className="space-y-1 text-right">
              <LearnerMessage text={turn.text_es} />
              <Words label="Used" words={turn.used} />
              <Words label="New to you" words={turn.taught} />
            </div>
          ) : (
            <div key={turn.turn_no} className="space-y-1">
              <div className="max-w-[44rem] rounded-2xl rounded-tl-md border border-line bg-surface px-4 py-3">
                {turn.kind === 'translation' && (
                  <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted">Se dice</p>
                )}
                <p className="es whitespace-pre-wrap text-ink">{turn.text_es}</p>
              </div>
              {turn.note_en && (
                <Note title={turn.kind === 'translation' ? 'Why' : 'Note'}>{turn.note_en}</Note>
              )}
              <Words label="Taught" words={turn.taught} />
            </div>
          ),
        )}
        {summary && <SummaryCard summary={summary} />}
      </div>
    </div>
  )
}
