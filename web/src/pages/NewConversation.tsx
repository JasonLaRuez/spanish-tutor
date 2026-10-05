import { useRef, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { AccentKeyboard } from '../components/AccentKeyboard'
import { useConversations } from '../state/context'

// The most topic words that can be taught first (MAX_WORDS in topics.py).
const MAX_NEW_WORDS = 20

const TOPICS = ['el jardín', 'la comida', 'el tiempo', 'los deportes', 'mi familia', 'el trabajo', 'los viajes']

export function NewConversation() {
  const { start } = useConversations()
  const navigate = useNavigate()
  const [topic, setTopic] = useState('')
  const [newWords, setNewWords] = useState(5)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const field = useRef<HTMLInputElement>(null)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setStarting(true)
    setError(null)
    try {
      const trimmed = topic.trim() || null
      const sessionId = await start(trimmed, trimmed ? newWords : 0)
      navigate(`/chat/${sessionId}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setStarting(false)
    }
  }

  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <h1 className="text-2xl font-semibold text-ink">New conversation</h1>
      <p className="mt-1 text-ink-2">
        The tutor talks inside the words you know and teaches new ones as they come up.
      </p>

      <form onSubmit={submit} className="mt-8 space-y-6">
        <div className="space-y-2">
          <label htmlFor="topic" className="block font-medium text-ink">
            ¿De qué quieres hablar?
          </label>
          <input
            id="topic"
            ref={field}
            value={topic}
            onChange={(event) => setTopic(event.target.value)}
            placeholder="A topic, or leave it empty to chat about anything"
            className="es w-full rounded-lg border border-line bg-surface px-3 py-2 text-ink placeholder:text-muted focus:border-accent focus:outline-none"
          />
          <AccentKeyboard target={field} value={topic} onChange={setTopic} />
          <div className="flex flex-wrap gap-1.5 pt-1">
            {TOPICS.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                onClick={() => setTopic(suggestion)}
                className="rounded-full border border-line px-3 py-1 text-sm text-ink-2 hover:bg-surface-2"
              >
                {suggestion}
              </button>
            ))}
          </div>
        </div>

        <div className={`space-y-2 ${topic.trim() ? '' : 'opacity-50'}`}>
          <label htmlFor="new-words" className="block font-medium text-ink">
            New words to learn first: up to <span className="tabular">{newWords}</span>
          </label>
          <input
            id="new-words"
            type="range"
            min={2}
            max={MAX_NEW_WORDS}
            value={newWords}
            disabled={!topic.trim()}
            onChange={(event) => setNewWords(Number(event.target.value))}
            className="w-full accent-[var(--accent)]"
          />
          <p className="text-sm text-muted">
            Topic words are chosen from real sentences about the topic and taught before you
            start. A broad topic may get fewer than you ask for; you’ll see why.
          </p>
        </div>

        <button
          type="submit"
          disabled={starting}
          className="rounded-lg bg-accent px-5 py-2.5 font-medium text-accent-ink disabled:opacity-50"
        >
          {starting ? 'Preparing…' : 'Empezar'}
        </button>
        {starting && (
          <p role="status" className="text-sm text-muted">
            {topic.trim()
              ? 'Choosing today’s words and starting the conversation (about 15 seconds)…'
              : 'Starting the conversation…'}
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
      </form>
    </div>
  )
}
