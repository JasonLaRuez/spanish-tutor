import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Lesson, Turn, Voice } from '../api/client'
import { LessonCard } from '../components/LessonCard'
import { SpanishText, TutorMessage } from '../components/Messages'
import { SpeakButton } from '../components/SpeakButton'
import { SpeechSettings } from '../components/SpeechSettings'
import { useNarration } from '../lib/useNarration'
import { ChatView } from '../pages/Chat'
import type { Chat } from '../state/context'
import { ConversationsProvider } from '../state/conversations'
import { SpeechProvider } from '../state/speech'
import { speechUrl, type AudioLike } from '../state/speechContext'

/** Stands in for an <audio> element: records what it was asked to play; `end()` finishes
 *  the clip as the browser would. */
class FakeAudio {
  src = ''
  playbackRate = 1
  played: { text: string; accent: string; rate: number }[] = []
  onended: (() => void) | null = null
  onerror: (() => void) | null = null
  paused = 0
  unlocked = 0 // silent clips played on the first tap (iPhone Safari)
  play() {
    if (this.src.startsWith('data:')) {
      this.unlocked++
      return Promise.resolve()
    }
    const query = new URLSearchParams(this.src.split('?')[1])
    this.played.push({ text: query.get('text')!, accent: query.get('accent')!, rate: this.playbackRate })
    return Promise.resolve()
  }
  pause() {
    this.paused++
  }
  end() {
    act(() => this.onended?.())
  }
  get texts() {
    return this.played.map((clip) => clip.text)
  }
}

const BOTH: Voice[] = [
  { accent: 'mx', label: 'México', available: true },
  { accent: 'es', label: 'España', available: true },
]

let audio: FakeAudio

function withSpeech(children: ReactNode, voices: Voice[] = BOTH) {
  return (
    <SpeechProvider createAudio={() => audio as unknown as AudioLike} loadVoices={() => Promise.resolve(voices)}>
      {children}
    </SpeechProvider>
  )
}

beforeEach(() => {
  audio = new FakeAudio()
  localStorage.clear()
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('')) // prefetches
})
afterEach(() => vi.restoreAllMocks())

const lesson: Lesson = {
  lexeme_id: 7,
  lemma: 'regar',
  pos: 'VERB',
  definition_en: 'to water',
  example: { es: 'Riego las plantas.', en: 'I water the plants.', source: 'tatoeba:42', author: 'ana', glosses: [] },
  model_written: false,
  practice: false,
}

const turn = (overrides: Partial<Turn> = {}): Turn => ({
  kind: 'conversation',
  reply_es: '¿Riegas tu jardín?',
  reply_en: 'Do you water your garden?',
  note_en: null,
  lessons: [],
  not_words: [],
  used: [],
  pending: null,
  ...overrides,
})

describe('Speech settings', () => {
  it('start as Mexican Spanish, read aloud, normal speed', async () => {
    render(withSpeech(<SpeechSettings />))
    expect(await screen.findByRole('button', { name: 'Accent: México' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Read replies aloud: on' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Speed: 1×' })).toBeInTheDocument()
  })

  it('toggle the accent, autoplay and speed, and remember them', async () => {
    const user = userEvent.setup()
    render(withSpeech(<SpeechSettings />))
    await user.click(await screen.findByRole('button', { name: 'Accent: México' }))
    await user.click(screen.getByRole('button', { name: 'Read replies aloud: on' }))
    await user.click(screen.getByRole('button', { name: 'Speed: 1×' }))
    expect(screen.getByRole('button', { name: 'Accent: España' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Speed: 0.75×' })).toBeInTheDocument()
    expect(JSON.parse(localStorage.getItem('spanish-tutor-speech')!)).toEqual({
      accent: 'es',
      autoplay: false,
      rate: 0.75,
    })
  })

  it('say how to install the voices when none is downloaded', async () => {
    render(withSpeech(<SpeechSettings />, [{ accent: 'mx', label: 'México', available: false }]))
    expect(await screen.findByText('Audio: voices not installed')).toHaveAttribute(
      'title',
      expect.stringContaining('spanish_tutor.speech download'),
    )
  })
})

describe('Listening to words and replies', () => {
  it('a clicked word is said as written, in the chosen accent and speed', async () => {
    localStorage.setItem('spanish-tutor-speech', JSON.stringify({ accent: 'es', autoplay: true, rate: 0.75 }))
    const user = userEvent.setup()
    render(withSpeech(<SpanishText text="¿Riegas tu jardín?" onLookUp={() => Promise.resolve(lesson)} />))
    await screen.findByRole('button', { name: 'Riegas' })
    await act(async () => {}) // the voices list has loaded
    await user.click(screen.getByRole('button', { name: 'Riegas' }))
    await waitFor(() => expect(audio.played).toEqual([{ text: 'Riegas', accent: 'es', rate: 0.75 }]))
  })

  it('a tutor message has a listen button that plays the reply, then stops it', async () => {
    const user = userEvent.setup()
    render(withSpeech(<TutorMessage turn={turn()} />))
    const listen = await screen.findByRole('button', { name: 'Listen to the tutor' })
    await user.click(listen)
    expect(audio.texts).toEqual(['¿Riegas tu jardín?'])
    await user.click(screen.getByRole('button', { name: 'Stop' }))
    expect(audio.paused).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Listen to the tutor' })).toBeInTheDocument()
  })

  it('lesson cards say the word and the example', async () => {
    const user = userEvent.setup()
    render(withSpeech(<LessonCard lesson={lesson} />))
    await user.click(await screen.findByRole('button', { name: 'Listen: regar' }))
    await user.click(screen.getByRole('button', { name: 'Listen to the example' }))
    expect(audio.texts).toEqual(['regar', 'Riego las plantas.'])
  })

  it('the first tap plays a silent clip, so iPhone Safari lets replies play later', async () => {
    const user = userEvent.setup()
    render(withSpeech(<p>page</p>))
    expect(audio.unlocked).toBe(0)
    await user.click(screen.getByText('page'))
    await user.click(screen.getByText('page'))
    expect(audio.unlocked).toBe(1) // once
    expect(audio.played).toEqual([])
  })

  it('no listen buttons when the chosen accent has no voice', async () => {
    render(withSpeech(<SpeakButton text="hola" />, [{ accent: 'mx', label: 'México', available: false }]))
    await act(async () => {}) // let the voices list load
    expect(screen.queryByRole('button')).toBeNull()
  })
})

describe('Reading replies aloud', () => {
  const chat = (overrides: Partial<Chat> = {}): Chat => ({
    sessionId: 7,
    topic: 'el jardín',
    items: [],
    taught: [],
    focus: [],
    used: [],
    pending: false,
    closed: false,
    ...overrides,
  })
  const renderChat = (value: Chat) =>
    render(
      withSpeech(
        <MemoryRouter>
          <ConversationsProvider>
            <ChatView chat={value} />
          </ConversationsProvider>
        </MemoryRouter>,
      ),
    )

  it('a reply that just arrived is read aloud once', async () => {
    const view = renderChat(chat({ items: [{ kind: 'tutor', id: 9001, turn: turn(), live: true }] }))
    await waitFor(() => expect(audio.texts).toEqual(['¿Riegas tu jardín?']))
    view.unmount()
    renderChat(chat({ items: [{ kind: 'tutor', id: 9001, turn: turn(), live: true }] })) // coming back
    await act(async () => {})
    expect(audio.texts).toEqual(['¿Riegas tu jardín?'])
  })

  it('a transcript rebuilt after a reload is not read aloud', async () => {
    renderChat(chat({ items: [{ kind: 'tutor', id: 9002, turn: turn() }] }))
    await screen.findByRole('button', { name: 'Listen to the tutor' })
    expect(audio.played).toEqual([])
  })

  it('nothing is read aloud when the learner turned it off', async () => {
    localStorage.setItem('spanish-tutor-speech', JSON.stringify({ accent: 'mx', autoplay: false, rate: 1 }))
    renderChat(chat({ items: [{ kind: 'tutor', id: 9003, turn: turn(), live: true }] }))
    await screen.findByRole('button', { name: 'Listen to the tutor' })
    expect(audio.played).toEqual([])
  })
})

describe('Narration', () => {
  const UNITS = ['Un día un pollo entra en un bosque.', 'Una bellota cae.', 'El pollo corre.']

  function Narrator({ units = UNITS }: { units?: string[] }) {
    const narration = useNarration(units, false, new Set([units.length - 1]))
    return (
      <div>
        <p data-testid="state">
          {narration.status} {narration.at ?? '-'}
        </p>
        <button onClick={() => narration.play(0)}>play</button>
        <button onClick={() => narration.play(1)}>from 2</button>
        <button onClick={narration.pause}>pause</button>
        <button onClick={narration.resume}>resume</button>
        <SpeakButton text="pollo" label="word" />
      </div>
    )
  }

  it('reads the units in order, one at a time, then stops', async () => {
    const user = userEvent.setup()
    render(withSpeech(<Narrator />))
    await screen.findByRole('button', { name: 'word' }) // voices loaded
    await user.click(screen.getByRole('button', { name: 'play' }))
    expect(screen.getByTestId('state')).toHaveTextContent('playing 0')
    expect(audio.texts).toEqual([UNITS[0]])
    audio.end()
    await waitFor(() => expect(audio.texts).toEqual(UNITS.slice(0, 2)))
    expect(screen.getByTestId('state')).toHaveTextContent('playing 1')
    audio.end()
    await waitFor(() => expect(audio.texts).toEqual(UNITS))
    audio.end()
    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('idle -'))
  })

  it('prefetches the next unit while one plays', async () => {
    const user = userEvent.setup()
    render(withSpeech(<Narrator />))
    await screen.findByRole('button', { name: 'word' })
    await user.click(screen.getByRole('button', { name: 'play' }))
    expect(globalThis.fetch).toHaveBeenCalledWith(speechUrl(UNITS[1], 'mx'))
  })

  it('starts from any unit', async () => {
    const user = userEvent.setup()
    render(withSpeech(<Narrator />))
    await screen.findByRole('button', { name: 'word' })
    await user.click(screen.getByRole('button', { name: 'from 2' }))
    expect(audio.texts).toEqual([UNITS[1]])
  })

  it('pauses where it was when something else plays, and resumes that unit', async () => {
    const user = userEvent.setup()
    render(withSpeech(<Narrator />))
    await screen.findByRole('button', { name: 'word' })
    await user.click(screen.getByRole('button', { name: 'play' }))
    audio.end()
    await waitFor(() => expect(audio.texts).toHaveLength(2))
    await user.click(screen.getByRole('button', { name: 'word' })) // a word, mid-sentence
    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('paused 1'))
    await user.click(screen.getByRole('button', { name: 'resume' }))
    expect(audio.texts).toEqual([UNITS[0], UNITS[1], 'pollo', UNITS[1]])
  })

  it('the pause button stops the clip and keeps the place', async () => {
    const user = userEvent.setup()
    render(withSpeech(<Narrator />))
    await screen.findByRole('button', { name: 'word' })
    await user.click(screen.getByRole('button', { name: 'play' }))
    await user.click(screen.getByRole('button', { name: 'pause' }))
    expect(screen.getByTestId('state')).toHaveTextContent('paused 0')
    expect(audio.paused).toBeGreaterThan(0)
  })
})
