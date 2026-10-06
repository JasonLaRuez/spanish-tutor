import { useSpeech } from '../state/speechContext'

/** Play `text` aloud (or stop it while it plays). Hidden when no voice is installed. */
export function SpeakButton({ text, label = 'Listen' }: { text: string; label?: string }) {
  const { available, playing, say, stop } = useSpeech()
  if (!available) return null
  const active = playing === text
  return (
    <button
      type="button"
      onClick={() => (active ? stop() : say(text))}
      aria-label={active ? 'Stop' : label}
      title={active ? 'Stop' : label}
      aria-pressed={active}
      className={`inline-grid h-6 w-6 shrink-0 place-items-center rounded-full text-xs hover:bg-surface-2 ${
        active ? 'text-accent-text' : 'text-muted hover:text-ink'
      }`}
    >
      <span aria-hidden>{active ? '■' : '▶'}</span>
    </button>
  )
}
