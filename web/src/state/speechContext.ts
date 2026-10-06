// Spanish speech (the API's Piper voices): the settings, the context and its hook. The
// provider (one shared player) is in speech.tsx.
import { createContext, useContext } from 'react'
import type { Voice } from '../api/client'

export type Accent = Voice['accent']

export interface SpeechSettings {
  accent: Accent
  autoplay: boolean // read each new tutor reply aloud once its text appears
  rate: number // playback speed; the browser keeps the pitch
}

export const RATES = [1, 0.75]
export const DEFAULTS: SpeechSettings = { accent: 'mx', autoplay: true, rate: 1 }
const SETTINGS_KEY = 'spanish-tutor-speech'

export function readSettings(): SpeechSettings {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? '{}')
    return {
      accent: saved.accent === 'es' ? 'es' : 'mx',
      autoplay: typeof saved.autoplay === 'boolean' ? saved.autoplay : DEFAULTS.autoplay,
      rate: RATES.includes(saved.rate) ? saved.rate : DEFAULTS.rate,
    }
  } catch {
    return DEFAULTS
  }
}

export function saveSettings(settings: SpeechSettings) {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings))
  } catch {
    // storage unavailable: the choice lasts for this visit only
  }
}

export const speechUrl = (text: string, accent: Accent) =>
  `/api/speech?${new URLSearchParams({ text, accent })}`

/** What the player needs from an audio element (HTMLAudioElement; a fake in tests). */
export type AudioLike = Pick<HTMLAudioElement, 'src' | 'playbackRate' | 'play' | 'pause' | 'onended' | 'onerror'>

export interface Speech {
  settings: SpeechSettings
  update: (change: Partial<SpeechSettings>) => void
  voices: Voice[]
  /** The chosen accent's voice is installed, so there's something to play. */
  available: boolean
  /** The key of the clip playing now (its text unless given), or null. */
  playing: string | null
  /** Say `text`; resolves true when it played to the end, false if stopped or failed. */
  say: (text: string, key?: string) => Promise<boolean>
  stop: () => void
  /** Ask for a clip ahead of time, so it's in the browser's cache when needed. */
  prefetch: (text: string) => void
}

const SILENT: Speech = {
  settings: DEFAULTS,
  update: () => {},
  voices: [],
  available: false,
  playing: null,
  say: async () => false,
  stop: () => {},
  prefetch: () => {},
}

export const SpeechContext = createContext<Speech | null>(null)

/** The speech context; outside a provider (components tested alone) everything is silent. */
export function useSpeech(): Speech {
  return useContext(SpeechContext) ?? SILENT
}

