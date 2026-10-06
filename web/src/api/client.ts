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
export type Summary = Schemas['SummaryOut']
export type Ended = Schemas['Ended']
export type Recommendations = Schemas['Recommendations']
export type RecommendedItem = Schemas['RecommendedItem']
export type RecommendedBook = Schemas['RecommendedBook']
export type Pick = Schemas['Pick']
export type CatalogItem = Schemas['CatalogItem']
export type ContentDetail = Schemas['ContentDetail']
export type NewWord = Schemas['NewWord']
export type ReadingState = Schemas['ReadingState']
export type StudyBatch = Schemas['StudyBatch']
export type StudyWord = Schemas['StudyWord']
export type ReadingLookUp = Schemas['ReadingLookUp']
export type SongTranslation = Schemas['SongTranslationOut']
export type TranslatedLine = Schemas['TranslatedLine']
export type Expression = Schemas['ExpressionOut']
export type ComparedLine = Schemas['ComparedLine']
export type Voice = Schemas['VoiceOut']
export type RatingQueue = Schemas['RatingQueue']
export type RatingItem = Schemas['RatingItem']
export type EvalOverview = Schemas['EvalOverview']
export type RateOut = Schemas['RateOut']
/** How an item was chosen: the default suggestion (or a surprise), or the learner's own pick. */
export type ChosenVia = Schemas['StartReading']['chosen_via']

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
  // Speech: which accents' voices are installed (the audio itself is /api/speech?text=…).
  voices: () => request<Voice[]>('/api/speech/voices'),
  // The evaluation's hand ratings: the overview, one type's items, and a rating.
  evalOverview: () => request<EvalOverview>('/api/eval'),
  ratingQueue: (itemType: string) => request<RatingQueue>(`/api/eval/items/${itemType}`),
  rate: (itemId: number, rating: { label?: string; score?: number }) =>
    post<{ ok: boolean }>('/api/eval/ratings', { item_id: itemId, ...rating }),
  sessions: () => request<SessionSummary[]>('/api/sessions'),
  transcript: (sessionId: number) => request<Transcript>(`/api/sessions/${sessionId}`),
  start: (topic: string | null, newWords: number) =>
    post<SessionStarted>('/api/sessions', { topic, new_words: newWords }),
  send: (sessionId: number, text: string) =>
    post<MessageReply>(`/api/sessions/${sessionId}/messages`, { text }),
  lookUp: (sessionId: number, word: string) =>
    post<Lesson>(`/api/sessions/${sessionId}/lookup`, { word }),
  end: (sessionId: number) => post<Ended>(`/api/sessions/${sessionId}/end`, {}),
  recommend: () => request<Recommendations>('/api/recommend'),
  surprise: () => request<Pick>('/api/recommend/surprise'),
  catalog: () => request<CatalogItem[]>('/api/content'),
  content: (contentId: number) => request<ContentDetail>(`/api/content/${contentId}`),
  startReading: (contentId: number, chosenVia: ChosenVia) =>
    post<{ ok: boolean }>(`/api/content/${contentId}/start`, { chosen_via: chosenVia }),
  finishReading: (contentId: number) =>
    post<{ ok: boolean }>(`/api/content/${contentId}/finish`, {}),
  // A reading session: study the new words in batches, read, finish, then talk about it.
  readingStart: (contentId: number, chosenVia: ChosenVia) =>
    post<ReadingState>('/api/reading', { content_id: contentId, chosen_via: chosenVia }),
  reading: (sessionId: number) => request<ReadingState>(`/api/reading/${sessionId}`),
  readingBatch: (sessionId: number, n = 20) =>
    request<StudyBatch>(`/api/reading/${sessionId}/batch?n=${n}`),
  readingStudy: (sessionId: number, lexemeIds: number[]) =>
    post<ReadingState>(`/api/reading/${sessionId}/study`, { lexeme_ids: lexemeIds }),
  readingLookUp: (sessionId: number, word: string) =>
    post<ReadingLookUp>(`/api/reading/${sessionId}/lookup`, { word }),
  readingFinish: (sessionId: number) => post<ReadingState>(`/api/reading/${sessionId}/finish`, {}),
  discuss: (sessionId: number) => post<SessionStarted>(`/api/reading/${sessionId}/discuss`, {}),
  // Songs and poems (the lyrics skill): the stored translation (made once, a model call the
  // first time), and the learner's own attempts compared with it (one call per submission).
  translation: (sessionId: number) => request<SongTranslation>(`/api/reading/${sessionId}/translation`),
  attempt: (sessionId: number, attempts: { line_no: number; text: string }[]) =>
    post<{ lines: ComparedLine[] }>(`/api/reading/${sessionId}/attempt`, { attempts }),
}
