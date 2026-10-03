import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'
import { insertAt } from '../lib/text'

const LOWER = ['á', 'é', 'í', 'ó', 'ú', 'ü', 'ñ', '¿', '¡']
const UPPER = ['Á', 'É', 'Í', 'Ó', 'Ú', 'Ü', 'Ñ', '¿', '¡']

interface Props {
  target: RefObject<HTMLTextAreaElement | HTMLInputElement | null>
  value: string
  onChange: (value: string) => void
}

/** Spanish characters that insert at the cursor of `target` (replacing any selection).
 *  Buttons keep the text field focused, so typing continues where it left off. */
export function AccentKeyboard({ target, value, onChange }: Props) {
  const [upper, setUpper] = useState(false)
  // A field that has never had focus reports its cursor at 0; insert at the end instead.
  const focused = useRef(false)

  useEffect(() => {
    const field = target.current
    if (!field) return
    const mark = () => {
      focused.current = true
    }
    field.addEventListener('focus', mark)
    return () => field.removeEventListener('focus', mark)
  }, [target])

  // Where the cursor goes once React has written the new value. Restored in a layout
  // effect, right after the update and before the next keystroke can arrive (a later
  // animation frame lost the race against fast typing).
  const caret = useRef<number | null>(null)
  useLayoutEffect(() => {
    const field = target.current
    if (caret.current === null || !field) return
    field.focus()
    field.setSelectionRange(caret.current, caret.current)
    caret.current = null
  }, [value, target])

  const insert = (char: string) => {
    const field = target.current
    const known = field && focused.current
    const start = known ? (field.selectionStart ?? value.length) : value.length
    const end = known ? (field.selectionEnd ?? value.length) : value.length
    const next = insertAt(value, start, end, char)
    caret.current = next.caret
    onChange(next.value)
  }

  return (
    <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Spanish characters">
      {(upper ? UPPER : LOWER).map((char) => (
        <button
          key={char}
          type="button"
          // Don't take focus from the text field: its cursor position is where we insert.
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => insert(char)}
          className="h-8 min-w-8 rounded-md border border-line bg-surface px-2 text-base text-ink hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-accent"
          aria-label={`Insert ${char}`}
        >
          {char}
        </button>
      ))}
      <button
        type="button"
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => setUpper((u) => !u)}
        aria-pressed={upper}
        className={`h-8 rounded-md border px-2 text-sm ${
          upper
            ? 'border-accent bg-accent text-accent-ink'
            : 'border-line bg-surface text-ink-2 hover:bg-surface-2'
        }`}
        aria-label="Capital letters"
        title="Capital letters"
      >
        ⇧
      </button>
    </div>
  )
}
