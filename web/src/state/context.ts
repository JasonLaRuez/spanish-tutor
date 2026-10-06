import { createContext, useContext } from 'react'
import type { Lesson, Summary, Transcript, Turn } from '../api/client'

export type ChatItem =
  // Topic words taught before the conversation; fewer than requested comes with a reason.
  | { kind: 'lessons'; id: number; lessons: Lesson[]; requested: number; shortfall: string | null }
  // live: arrived in this visit (read aloud if autoplay is on), not rebuilt from a transcript.
  | { kind: 'tutor'; id: number; turn: Turn; live?: boolean }
  | { kind: 'learner'; id: number; text: string }
  | { kind: 'error'; id: number; text: string }
  | { kind: 'summary'; id: number; summary: Summary } // the end of the conversation

export interface Chat {
  sessionId: number
  topic: string | null
  items: ChatItem[]
  taught: string[]
  focus: string[] // today's topic words, for the learner to practice
  used: string[] // every word the learner has used in this conversation
  pending: boolean
  closed: boolean // ended, or the server no longer has it open (e.g. it restarted)
}

export interface Conversations {
  chats: Record<number, Chat>
  start: (topic: string | null, newWords: number) => Promise<number>
  /** Talk about a text just read: the reading session continues as a conversation. */
  discuss: (readingSessionId: number) => Promise<number>
  send: (sessionId: number, text: string) => Promise<void>
  lookUp: (sessionId: number, word: string) => Promise<Lesson>
  /** End the conversation without a typed goodbye (the "Hasta luego" button). */
  end: (sessionId: number) => Promise<void>
  /** Rebuild an open conversation from its saved transcript (after a page reload). */
  adopt: (transcript: Transcript) => void
}

export const Context = createContext<Conversations | null>(null)

export function useConversations(): Conversations {
  const value = useContext(Context)
  if (!value) throw new Error('useConversations must be used inside ConversationsProvider')
  return value
}
