/** Insert `text` over the selection [start, end) of `value`; returns the new value and
 *  where the caret goes (just after the inserted text). */
export function insertAt(
  value: string,
  start: number,
  end: number,
  text: string,
): { value: string; caret: number } {
  const from = Math.max(0, Math.min(start, value.length))
  const to = Math.max(from, Math.min(end, value.length))
  return { value: value.slice(0, from) + text + value.slice(to), caret: from + text.length }
}

/** Split text into words (letters, including accented ones) and everything in between,
 *  so words can be made clickable while spacing and punctuation stay as written. */
export function splitWords(text: string): { text: string; word: boolean }[] {
  const parts: { text: string; word: boolean }[] = []
  let last = 0
  for (const match of text.matchAll(/\p{L}+/gu)) {
    const at = match.index ?? 0
    if (at > last) parts.push({ text: text.slice(last, at), word: false })
    parts.push({ text: match[0], word: true })
    last = at + match[0].length
  }
  if (last < text.length) parts.push({ text: text.slice(last), word: false })
  return parts
}

const POS_NAMES: Record<string, string> = {
  ADJ: 'adjective',
  ADP: 'preposition',
  ADV: 'adverb',
  CCONJ: 'conjunction',
  DET: 'determiner',
  EXPR: 'expression',
  INTJ: 'interjection',
  NOUN: 'noun',
  NUM: 'number',
  PART: 'particle',
  PRON: 'pronoun',
  SCONJ: 'conjunction',
  VERB: 'verb',
}

export const posName = (pos: string) => POS_NAMES[pos] ?? pos.toLowerCase()

/** "2026-10-02 17:09:26" (UTC, as SQLite stores it) -> a local date and time. */
export function when(timestamp: string | null | undefined): string {
  if (!timestamp) return ''
  const date = new Date(timestamp.replace(' ', 'T') + 'Z')
  if (Number.isNaN(date.getTime())) return timestamp
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export const formatNumber = (n: number) => n.toLocaleString()
