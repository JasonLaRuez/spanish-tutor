import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { Link, useParams } from 'react-router'
import { api } from '../api/client'
import { AccentKeyboard } from '../components/AccentKeyboard'
import { LearnerMessage, TutorMessage, TypingIndicator } from '../components/Messages'
import { SummaryCard } from '../components/SummaryCard'
import { PreTaught, TodaysWords } from '../components/TodaysWords'
import { useConversations, type Chat } from '../state/context'
import { useSpeech } from '../state/speechContext'

const HOW_TO_SAY = '¿Cómo se dice ""?'
// Tutor replies already read aloud (item ids are unique for the whole visit).
const spoken = new Set<number>()

export function ChatPage() {
  const sessionId = Number(useParams().sessionId)
  const { chats, adopt } = useConversations()
  const chat = chats[sessionId]
  const [missing, setMissing] = useState<string | null>(null)

  // After a page reload the chat isn't in memory: rebuild it from the transcript.
  useEffect(() => {
    if (chat || !Number.isFinite(sessionId)) return
    api
      .transcript(sessionId)
      .then(adopt)
      .catch((error) => setMissing(error instanceof Error ? error.message : String(error)))
  }, [chat, sessionId, adopt])

  if (!chat) {
    return (
      <div className="p-8 text-ink-2">
        {missing ? (
          <p>
            {missing} <Link to="/new" className="text-accent-text underline">Start a conversation</Link>
          </p>
        ) : (
          <p>Loading the conversation…</p>
        )}
      </div>
    )
  }
  return <ChatView chat={chat} />
}

export function ChatView({ chat }: { chat: Chat }) {
  const { send, lookUp, end: endConversation } = useConversations()
  const [text, setText] = useState('')
  const [showTaught, setShowTaught] = useState(false)
  const [showToday, setShowToday] = useState(true)
  const usedToday = chat.focus.filter((word) => chat.used.includes(word)).length
  const field = useRef<HTMLTextAreaElement>(null)
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => {
    end.current?.scrollIntoView?.({ behavior: 'smooth', block: 'end' })
  }, [chat.items.length, chat.pending])

  // Read the newest reply aloud once its text is on the page (if autoplay is on). Only
  // replies that arrived in this visit, each once: not a transcript rebuilt after a
  // reload, and not again when the learner comes back to the page.
  const speech = useSpeech()
  const { autoplay } = speech.settings
  const { say, available } = speech
  useEffect(() => {
    if (!available) return // the voices are still loading (or none is installed)
    const fresh = chat.items.filter(
      (item) => item.kind === 'tutor' && item.live && !spoken.has(item.id),
    )
    fresh.forEach((item) => spoken.add(item.id))
    const newest = fresh.at(-1)
    if (autoplay && newest?.kind === 'tutor') say(newest.turn.reply_es)
  }, [chat.items, autoplay, say, available])

  // The "¿cómo se dice?" template itself, with nothing between the quotes, isn't a question.
  const canSend = !chat.pending && !chat.closed && text.trim().length > 0 && text.trim() !== HOW_TO_SAY

  // Cursor placement after the template is filled in, applied as soon as React has
  // written it (see AccentKeyboard for why not an animation frame).
  const caret = useRef<number | null>(null)
  useLayoutEffect(() => {
    if (caret.current === null || !field.current) return
    field.current.focus()
    field.current.setSelectionRange(caret.current, caret.current)
    caret.current = null
  }, [text])

  const submit = (event?: FormEvent) => {
    event?.preventDefault()
    if (!canSend) return
    send(chat.sessionId, text.trim())
    setText('')
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      submit()
    }
  }

  const askHowToSay = () => {
    caret.current = HOW_TO_SAY.length - 2 // between the quotes
    setText(HOW_TO_SAY)
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b border-line bg-surface px-6 py-3">
        <div className="min-w-0 flex-1">
          <h1 className="truncate font-semibold text-ink">{chat.topic ?? 'Open conversation'}</h1>
          <p className="text-xs text-muted">Click any Spanish word to look it up.</p>
        </div>
        {chat.focus.length > 0 && (
          <button
            type="button"
            onClick={() => setShowToday((shown) => !shown)}
            aria-expanded={showToday}
            className="rounded-md border border-line px-2.5 py-1 text-sm text-ink-2 hover:bg-surface-2"
          >
            Today’s words: {usedToday} of {chat.focus.length} used
          </button>
        )}
        <button
          type="button"
          onClick={() => setShowTaught((shown) => !shown)}
          aria-expanded={showTaught}
          className="rounded-md border border-line px-2.5 py-1 text-sm text-ink-2 hover:bg-surface-2"
        >
          Words taught: {chat.taught.length}
        </button>
      </header>

      {showToday && chat.focus.length > 0 && (
        <div className="border-b border-line bg-surface px-6 py-2.5">
          <TodaysWords focus={chat.focus} used={chat.used} />
        </div>
      )}

      {showTaught && (
        <div className="border-b border-line bg-surface-2 px-6 py-2 text-sm text-ink-2">
          {chat.taught.length ? chat.taught.join(', ') : 'None yet.'}
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        <div className="mx-auto max-w-3xl space-y-5">
          {chat.items.map((item) => {
            switch (item.kind) {
              case 'lessons':
                return (
                  <PreTaught
                    key={item.id}
                    lessons={item.lessons}
                    requested={item.requested}
                    shortfall={item.shortfall}
                  />
                )
              case 'tutor':
                return (
                  <TutorMessage
                    key={item.id}
                    turn={item.turn}
                    onLookUp={chat.closed ? undefined : (word) => lookUp(chat.sessionId, word)}
                  />
                )
              case 'learner':
                return <LearnerMessage key={item.id} text={item.text} />
              case 'summary':
                return <SummaryCard key={item.id} summary={item.summary} />
              case 'error':
                return (
                  <p key={item.id} role="alert" className="text-sm text-danger">
                    {item.text}
                  </p>
                )
            }
          })}
          {chat.pending && <TypingIndicator />}
          <div ref={end} />
        </div>
      </div>

      <form onSubmit={submit} className="border-t border-line bg-surface px-6 py-3">
        <div className="mx-auto max-w-3xl space-y-2">
          {chat.closed ? (
            <p className="text-sm text-ink-2">
              This conversation has ended.{' '}
              <Link to="/history" className="text-accent-text underline">
                History
              </Link>
              {' · '}
              <Link to="/new" className="text-accent-text underline">
                Start a new one
              </Link>
            </p>
          ) : (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <AccentKeyboard target={field} value={text} onChange={setText} />
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={askHowToSay}
                    className="rounded-md border border-line px-2.5 py-1 text-sm text-ink-2 hover:bg-surface-2"
                  >
                    ¿Cómo se dice…?
                  </button>
                  <button
                    type="button"
                    onClick={() => endConversation(chat.sessionId)}
                    disabled={chat.pending}
                    title="End the conversation and see a summary (or just write “¡Hasta luego!”)"
                    className="rounded-md border border-line px-2.5 py-1 text-sm text-ink-2 hover:bg-surface-2 disabled:opacity-40"
                  >
                    ¡Hasta luego!
                  </button>
                </div>
              </div>
              <div className="flex items-end gap-2">
                <label htmlFor="message" className="sr-only">
                  Your message
                </label>
                <textarea
                  id="message"
                  ref={field}
                  value={text}
                  onChange={(event) => setText(event.target.value)}
                  onKeyDown={onKeyDown}
                  rows={2}
                  placeholder="Escribe en español…  ('a → á, ~n → ñ, ?palabra → ¿palabra)"
                  className="es min-h-12 flex-1 resize-y rounded-lg border border-line bg-page px-3 py-2 text-ink placeholder:text-muted focus:border-accent focus:outline-none"
                />
                <button
                  type="submit"
                  disabled={!canSend}
                  className="h-11 rounded-lg bg-accent px-4 font-medium text-accent-ink disabled:opacity-40"
                >
                  Send
                </button>
              </div>
              <p className="text-xs text-muted">Enter to send · Shift+Enter for a new line</p>
            </>
          )}
        </div>
      </form>
    </div>
  )
}
