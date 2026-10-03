import { createContext, useContext } from 'react'
import type { Lesson, Transcript, Turn } from '../api/client'

export type ChatItem =
  | { kind: 'lessons'; id: number; lessons: Lesson[] }
  | { kind: 'tutor'; id: number; turn: Turn }
  | { kind: 'learner'; id: number; text: string }
  | { kind: 'error'; id: number; text: string }

export interface Chat {
  sessionId: number
  topic: string | null
  items: ChatItem[]
  taught: string[]
  pending: boolean
  closed: boolean // the server no longer has it open (e.g. it restarted)
}

export interface Conversations {
  chats: Record<number, Chat>
  start: (topic: string | null, newWords: number) => Promise<number>
  send: (sessionId: number, text: string) => Promise<void>
  lookUp: (sessionId: number, word: string) => Promise<Lesson>
  /** Rebuild an open conversation from its saved transcript (after a page reload). */
  adopt: (transcript: Transcript) => void
}

export const Context = createContext<Conversations | null>(null)

export function useConversations(): Conversations {
  const value = useContext(Context)
  if (!value) throw new Error('useConversations must be used inside ConversationsProvider')
  return value
}
