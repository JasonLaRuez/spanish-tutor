import { api, type ComparedLine } from '../api/client'

/** Load the translation (a model call the first time a song is opened). */
export async function loadComparison(sessionId: number) {
  const translation = await api.translation(sessionId)
  const lines: ComparedLine[] = translation.lines.map((line) => ({
    ...line,
    attempt: null,
    verdict: null,
    comment_en: null,
  }))
  return { lines, expressions: translation.expressions }
}
