// Open conversations, kept above the routes so a reply that arrives while the learner is
// on another page (Progress, History) isn't lost, and the chat is still there on return.
import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { api, ApiError, type SessionStarted, type Transcript } from '../api/client'
import { Context, type Chat, type ChatItem } from './context'

let nextId = 1
const id = () => nextId++

export function ConversationsProvider({ children }: { children: ReactNode }) {
  const [chats, setChats] = useState<Record<number, Chat>>({})

  const update = useCallback((sessionId: number, change: (chat: Chat) => Chat) => {
    setChats((all) => (all[sessionId] ? { ...all, [sessionId]: change(all[sessionId]) } : all))
  }, [])

  // A chat from its opening: a new conversation, or the talk after a reading session.
  const open = useCallback((started: SessionStarted) => {
    const items: ChatItem[] = []
    if (started.lessons.length || started.shortfall)
      items.push({
        kind: 'lessons',
        id: id(),
        lessons: started.lessons,
        requested: started.requested_words,
        shortfall: started.shortfall,
      })
    items.push({ kind: 'tutor', id: id(), turn: started.opening })
    setChats((all) => ({
      ...all,
      [started.session_id]: {
        sessionId: started.session_id,
        topic: started.topic,
        items,
        taught: [
          ...started.lessons.map((lesson) => lesson.lemma),
          ...started.opening.lessons.map((lesson) => lesson.lemma),
        ],
        // One checklist entry per word: trabajador ADJ and NOUN are both taught, used as one.
        focus: [...new Set(started.lessons.map((lesson) => lesson.lemma))],
        used: [],
        pending: false,
        closed: false,
      },
    }))
    return started.session_id
  }, [])

  const start = useCallback(
    async (topic: string | null, newWords: number) => open(await api.start(topic, newWords)),
    [open],
  )

  const discuss = useCallback(
    async (readingSessionId: number) => open(await api.discuss(readingSessionId)),
    [open],
  )

  const send = useCallback(
    async (sessionId: number, text: string) => {
      const learnerId = id()
      update(sessionId, (chat) => ({
        ...chat,
        pending: true,
        items: [...chat.items, { kind: 'learner', id: learnerId, text }],
      }))
      try {
        const reply = await api.send(sessionId, text)
        update(sessionId, (chat) => ({
          ...chat,
          pending: false,
          taught: reply.taught,
          used: [...new Set([...chat.used, ...reply.turn.used])],
          // A goodbye ends the conversation: the reply carries its summary.
          closed: chat.closed || Boolean(reply.summary),
          items: [
            // Show the text as the server read it (accent markers expanded).
            ...chat.items.map((item) =>
              item.id === learnerId ? { ...item, text: reply.written } : item,
            ),
            { kind: 'tutor', id: id(), turn: reply.turn },
            ...(reply.summary ? [{ kind: 'summary' as const, id: id(), summary: reply.summary }] : []),
          ],
        }))
      } catch (error) {
        const closed = error instanceof ApiError && error.status === 404
        const message = error instanceof Error ? error.message : String(error)
        update(sessionId, (chat) => ({
          ...chat,
          pending: false,
          closed: chat.closed || closed,
          items: [...chat.items, { kind: 'error', id: id(), text: message }],
        }))
      }
    },
    [update],
  )

  const end = useCallback(
    async (sessionId: number) => {
      update(sessionId, (chat) => ({ ...chat, pending: true }))
      try {
        const ended = await api.end(sessionId)
        update(sessionId, (chat) => ({
          ...chat,
          pending: false,
          closed: true,
          items: [
            ...chat.items,
            { kind: 'tutor', id: id(), turn: ended.turn },
            { kind: 'summary', id: id(), summary: ended.summary },
          ],
        }))
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        update(sessionId, (chat) => ({
          ...chat,
          pending: false,
          closed: chat.closed || (error instanceof ApiError && error.status === 404),
          items: [...chat.items, { kind: 'error', id: id(), text: message }],
        }))
      }
    },
    [update],
  )

  // A looked-up word the learner didn't know is taught on the server; the session's
  // taught list catches up with the next reply.
  const lookUp = useCallback(
    (sessionId: number, word: string) => api.lookUp(sessionId, word),
    [],
  )

  const adopt = useCallback((transcript: Transcript) => {
    const { session, turns, summary } = transcript
    // Only the conversation's own turns: a reading session's study and reading turns
    // belong to the reading page, not the chat about the text.
    const said = turns.flatMap((turn) =>
      turn.kind === 'conversation' || turn.kind === 'translation' ? [{ ...turn, kind: turn.kind }] : [],
    )
    const items: ChatItem[] = said.map((turn) =>
      turn.role === 'learner'
        ? { kind: 'learner', id: id(), text: turn.text_es }
        : {
            kind: 'tutor',
            id: id(),
            // The transcript keeps the Spanish and the note; translations and lessons
            // were shown when the turn happened.
            turn: {
              kind: turn.kind,
              reply_es: turn.text_es,
              reply_en: null,
              note_en: turn.note_en,
              lessons: [],
              not_words: [],
              used: [],
              pending: null,
            },
          },
    )
    if (summary) items.push({ kind: 'summary', id: id(), summary })
    setChats((all) => ({
      ...all,
      [session.session_id]: {
        sessionId: session.session_id,
        topic: session.topic,
        items,
        taught: turns.flatMap((turn) => turn.taught),
        focus: [...new Set(turns.flatMap((turn) => turn.pre_taught))],
        used: [...new Set(turns.flatMap((turn) => turn.used))],
        pending: false,
        closed: !session.active,
      },
    }))
  }, [])

  const value = useMemo(
    () => ({ chats, start, discuss, send, lookUp, end, adopt }),
    [chats, start, discuss, send, lookUp, end, adopt],
  )
  return <Context.Provider value={value}>{children}</Context.Provider>
}
