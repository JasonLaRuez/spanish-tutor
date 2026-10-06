// The speech provider: the learner's settings, which voices are installed, and one
// shared player, so starting a clip stops whatever was playing.
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, type Voice } from '../api/client'
import {
  SpeechContext,
  readSettings,
  saveSettings,
  speechUrl,
  type AudioLike,
  type SpeechSettings,
} from './speechContext'

export function SpeechProvider({
  children,
  createAudio = () => new Audio(),
  loadVoices = api.voices,
}: {
  children: ReactNode
  createAudio?: () => AudioLike
  loadVoices?: () => Promise<Voice[]>
}) {
  const [settings, setSettings] = useState<SpeechSettings>(readSettings)
  const [voices, setVoices] = useState<Voice[]>([])
  const [playing, setPlaying] = useState<string | null>(null)
  const audio = useRef<AudioLike | null>(null)
  // Settles the clip in progress (false: it was stopped before the end).
  const settle = useRef<((ended: boolean) => void) | null>(null)

  useEffect(() => {
    loadVoices().then(setVoices).catch(() => setVoices([]))
  }, [loadVoices])

  const update = useCallback((change: Partial<SpeechSettings>) => {
    setSettings((current) => {
      const next = { ...current, ...change }
      saveSettings(next)
      return next
    })
  }, [])

  const available = voices.some((voice) => voice.accent === settings.accent && voice.available)

  const stop = useCallback(() => {
    audio.current?.pause()
    settle.current?.(false)
    settle.current = null
    setPlaying(null)
  }, [])

  const say = useCallback(
    (text: string, key = text) => {
      stop()
      if (!available || !text.trim()) return Promise.resolve(false)
      const player = (audio.current ??= createAudio())
      return new Promise<boolean>((resolve) => {
        const done = (ended: boolean) => {
          if (settle.current !== done) return // a later clip took over
          settle.current = null
          setPlaying(null)
          resolve(ended)
        }
        settle.current = done
        player.onended = () => done(true)
        player.onerror = () => done(false)
        player.src = speechUrl(text, settings.accent)
        player.playbackRate = settings.rate
        setPlaying(key)
        player.play().catch(() => done(false))
      })
    },
    [available, createAudio, settings.accent, settings.rate, stop],
  )

  const prefetch = useCallback(
    (text: string) => {
      if (available && text.trim()) fetch(speechUrl(text, settings.accent)).catch(() => {})
    },
    [available, settings.accent],
  )

  useEffect(() => stop, [stop]) // nothing keeps playing after the app goes away

  const value = useMemo(
    () => ({ settings, update, voices, available, playing, say, stop, prefetch }),
    [settings, update, voices, available, playing, say, stop, prefetch],
  )
  return <SpeechContext.Provider value={value}>{children}</SpeechContext.Provider>
}
