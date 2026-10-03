// A typed client for the tutor's API. The types come from the API's own OpenAPI schema
// (`npm run gen:api` regenerates schema.d.ts), so the UI can't drift from the server.
import type { components } from './schema'

type Schemas = components['schemas']
export type Lesson = Schemas['LessonOut']
export type Example = Schemas['ExampleOut']
export type Turn = Schemas['TurnOut']
export type SessionStarted = Schemas['SessionStarted']
export type MessageReply = Schemas['MessageReply']
export type SessionSummary = Schemas['SessionSummary']
export type Transcript = Schemas['Transcript']
export type TranscriptTurn = Schemas['TranscriptTurn']
export type Progress = Schemas['Progress']
export type Band = Schemas['Band']
export type Growth = Schemas['Growth']
export type GapWord = Schemas['GapWord']

/** An HTTP error from the API, with the server's message (FastAPI's `detail`). */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    let message = response.statusText || `HTTP ${response.status}`
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') message = body.detail
    } catch {
      // not JSON: keep the status text
    }
    throw new ApiError(response.status, message)
  }
  return (await response.json()) as T
}

const post = <T>(path: string, body: unknown) =>
  request<T>(path, { method: 'POST', body: JSON.stringify(body) })

export const api = {
  progress: () => request<Progress>('/api/progress'),
  sessions: () => request<SessionSummary[]>('/api/sessions'),
  transcript: (sessionId: number) => request<Transcript>(`/api/sessions/${sessionId}`),
  start: (topic: string | null, newWords: number) =>
    post<SessionStarted>('/api/sessions', { topic, new_words: newWords }),
  send: (sessionId: number, text: string) =>
    post<MessageReply>(`/api/sessions/${sessionId}/messages`, { text }),
  lookUp: (sessionId: number, word: string) =>
    post<Lesson>(`/api/sessions/${sessionId}/lookup`, { word }),
}
