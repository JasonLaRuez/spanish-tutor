import { RATES, useSpeech } from '../state/speechContext'

/** The listening settings: accent, reading replies aloud, speed (kept in this browser). */
export function SpeechSettings() {
  const { settings, update, voices } = useSpeech()
  if (!voices.some((voice) => voice.available)) {
    return (
      <p
        className="text-xs text-muted"
        title="Run: uv run python -m spanish_tutor.speech download"
      >
        Audio: voices not installed
      </p>
    )
  }
  const other = voices.find((voice) => voice.accent !== settings.accent)
  const label = voices.find((voice) => voice.accent === settings.accent)?.label ?? settings.accent
  const button = 'block text-xs text-muted hover:text-ink'
  return (
    <div className="space-y-1" aria-label="Listening settings" role="group">
      <button
        type="button"
        className={button}
        onClick={() => other && update({ accent: other.accent })}
        title="The accent of every voice in the app"
      >
        Accent: {label}
      </button>
      <button
        type="button"
        className={button}
        onClick={() => update({ autoplay: !settings.autoplay })}
        aria-pressed={settings.autoplay}
      >
        Read replies aloud: {settings.autoplay ? 'on' : 'off'}
      </button>
      <button
        type="button"
        className={button}
        onClick={() => update({ rate: RATES[(RATES.indexOf(settings.rate) + 1) % RATES.length] })}
      >
        Speed: {settings.rate}×
      </button>
    </div>
  )
}
