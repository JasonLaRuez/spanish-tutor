/** A song line's marked words (english.py): lowercase word -> 'english' (the song switching
 *  into English) or 'loanword' (an English word used as Spanish). */
export type LineMarks = Record<string, 'english' | 'loanword'>

/** The mark of a word as the reader splits it (letters only), on its line. A contraction
 *  is marked whole ("don't"), so each of its pieces ("don", "t") takes its mark. */
export function markOf(word: string, marks: LineMarks | undefined): 'english' | 'loanword' | undefined {
  if (!marks) return undefined
  const lower = word.toLowerCase()
  if (marks[lower]) return marks[lower]
  for (const [marked, kind] of Object.entries(marks)) {
    if (marked.split(/['’]/).includes(lower)) return kind
  }
  return undefined
}

/** Whether every word of a line is English: nothing in it to translate from Spanish. */
export function allEnglish(line: string, marks: LineMarks | undefined): boolean {
  const words = line.match(/\p{L}+/gu) ?? []
  return words.length > 0 && words.every((w) => markOf(w, marks) === 'english')
}
