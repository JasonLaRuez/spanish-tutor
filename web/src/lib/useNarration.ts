import { useCallback, useEffect, useRef, useState } from 'react'
import { useSpeech } from '../state/speechContext'

export type NarrationStatus = 'idle' | 'playing' | 'paused'

/** Pauses after each unit, in ms: prose sentences flow on; a poem's lines get a breath,
 *  and the end of a stanza a longer one. */
export const PAUSES = { sentence: 150, line: 350, stanza: 900 }

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

/** Read `units` (a text's sentences, or a poem's lines) aloud one at a time, from any of
 *  them. `breaks` are the units that end a paragraph or stanza. Anything else that starts
 *  playing (a clicked word, a lesson card) pauses the narration where it was. */
export function useNarration(units: string[], verse: boolean, breaks: Set<number>) {
  const { say, stop, prefetch } = useSpeech()
  const [at, setAt] = useState<number | null>(null) // the unit being read, or where it paused
  const [status, setStatus] = useState<NarrationStatus>('idle')
  const run = useRef(0) // each play() is a run; a newer run (or pause/stop) ends the older

  const play = useCallback(
    async (from: number) => {
      const me = ++run.current
      setStatus('playing')
      for (let i = from; i < units.length; i++) {
        setAt(i)
        if (i + 1 < units.length) prefetch(units[i + 1])
        const ended = await say(units[i], `narration:${i}`)
        if (run.current !== me) return
        if (!ended) {
          setStatus('paused') // interrupted: resume restarts this unit
          return
        }
        if (i + 1 < units.length) {
          await sleep(breaks.has(i) ? (verse ? PAUSES.stanza : PAUSES.line) : verse ? PAUSES.line : PAUSES.sentence)
          if (run.current !== me) return
        }
      }
      setStatus('idle')
      setAt(null)
    },
    [units, verse, breaks, say, prefetch],
  )

  const pause = useCallback(() => {
    run.current++
    stop()
    setStatus('paused')
  }, [stop])

  const reset = useCallback(() => {
    run.current++
    stop()
    setStatus('idle')
    setAt(null)
  }, [stop])

  // Leaving the page ends the narration.
  useEffect(
    () => () => {
      run.current++
      stop()
    },
    [stop],
  )

  return { at, status, play, pause, resume: () => play(at ?? 0), stop: reset }
}
