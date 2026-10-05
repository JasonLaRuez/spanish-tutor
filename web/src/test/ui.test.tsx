import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useRef, useState } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, type Lesson, type Summary, type Turn } from '../api/client'
import { AccentKeyboard } from '../components/AccentKeyboard'
import { toPoints } from '../lib/growth'
import { TutorMessage, TypingIndicator } from '../components/Messages'
import { SummaryCard } from '../components/SummaryCard'
import { PreTaught } from '../components/TodaysWords'
import { niceTicks } from '../lib/chart'
import { insertAt, splitWords } from '../lib/text'
import { ChatView } from '../pages/Chat'
import type { Chat } from '../state/context'
import { ConversationsProvider } from '../state/conversations'

const lesson: Lesson = {
  lexeme_id: 7,
  lemma: 'regar',
  pos: 'VERB',
  definition_en: 'to water',
  example: { es: 'Riego las plantas.', en: 'I water the plants.', source: 'tatoeba:42', author: 'ana', glosses: [] },
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

afterEach(() => vi.restoreAllMocks())

describe('text helpers', () => {
  it('inserts at the caret, replacing a selection', () => {
    expect(insertAt('el jardn', 7, 7, 'í')).toEqual({ value: 'el jardín', caret: 8 })
    expect(insertAt('hola xx', 5, 7, 'ñ')).toEqual({ value: 'hola ñ', caret: 6 })
    expect(insertAt('', 3, 3, '¿')).toEqual({ value: '¿', caret: 1 }) // clamped to the text
  })

  it('splits words from punctuation, keeping accents inside words', () => {
    expect(splitWords('¿Qué tal, señor?')).toEqual([
      { text: '¿', word: false },
      { text: 'Qué', word: true },
      { text: ' ', word: false },
      { text: 'tal', word: true },
      { text: ', ', word: false },
      { text: 'señor', word: true },
      { text: '?', word: false },
    ])
  })
})

function KeyboardHarness() {
  const [value, setValue] = useState('el jardn')
  const ref = useRef<HTMLTextAreaElement>(null)
  return (
    <>
      <textarea aria-label="text" ref={ref} value={value} onChange={(e) => setValue(e.target.value)} />
      <AccentKeyboard target={ref} value={value} onChange={setValue} />
    </>
  )
}

describe('AccentKeyboard', () => {
  it('inserts a character at the cursor', async () => {
    const user = userEvent.setup()
    render(<KeyboardHarness />)
    const field = screen.getByLabelText<HTMLTextAreaElement>('text')
    field.focus()
    field.setSelectionRange(7, 7)
    await user.click(screen.getByRole('button', { name: 'Insert í' }))
    expect(field.value).toBe('el jardín')
  })

  it('keeps typing where the character went in, even right after the click', async () => {
    // Regression: the cursor used to be restored a frame later, and fast typing landed first.
    const user = userEvent.setup()
    render(<KeyboardHarness />)
    const field = screen.getByLabelText<HTMLTextAreaElement>('text')
    field.focus()
    field.setSelectionRange(7, 7)
    await user.click(screen.getByRole('button', { name: 'Insert í' }))
    await user.keyboard('xy')
    expect(field.value).toBe('el jardíxyn')
  })

  it('appends at the end of a field that never had focus, and toggles capitals', async () => {
    const user = userEvent.setup()
    render(<KeyboardHarness />)
    const field = screen.getByLabelText<HTMLTextAreaElement>('text')
    await user.click(screen.getByRole('button', { name: 'Capital letters' }))
    await user.click(screen.getByRole('button', { name: 'Insert Ñ' }))
    expect(field.value).toBe('el jardnÑ')
  })
})

describe('TutorMessage', () => {
  it('shows the note and new words, and the English on request', async () => {
    const user = userEvent.setup()
    render(<TutorMessage turn={turn({ note_en: 'Use "regar".', lessons: [lesson] })} />)
    expect(screen.getByText('Use "regar".')).toBeInTheDocument()
    expect(screen.getByText('to water')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Tatoeba #42' })).toHaveAttribute(
      'href',
      'https://tatoeba.org/en/sentences/show/42',
    )
    expect(screen.queryByText('Do you water your garden?')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'English' }))
    expect(screen.getByText('Do you water your garden?')).toBeInTheDocument()
  })

  it('looks up a clicked word', async () => {
    const user = userEvent.setup()
    const onLookUp = vi.fn().mockResolvedValue(lesson)
    render(<TutorMessage turn={turn()} onLookUp={onLookUp} />)
    await user.click(screen.getByRole('button', { name: 'Riegas' }))
    expect(onLookUp).toHaveBeenCalledWith('Riegas')
    expect(await screen.findByText('to water')).toBeInTheDocument()
  })

  it('shows a translation with what the conversation was waiting for', () => {
    render(
      <TutorMessage
        turn={turn({ kind: 'translation', reply_es: 'el perro', reply_en: null, pending: '¿Tienes un perro?' })}
      />,
    )
    expect(screen.getByText('Se dice')).toBeInTheDocument()
    expect(screen.getByText('¿Tienes un perro?')).toBeInTheDocument()
  })
})

describe('ChatView', () => {
  const chat = (overrides: Partial<Chat> = {}): Chat => ({
    sessionId: 1,
    topic: 'el jardín',
    items: [{ kind: 'tutor', id: 1, turn: turn() }],
    taught: ['regar'],
    focus: [],
    used: [],
    pending: false,
    closed: false,
    ...overrides,
  })

  const renderChat = (value: Chat) =>
    render(
      <MemoryRouter>
        <ConversationsProvider>
          <ChatView chat={value} />
        </ConversationsProvider>
      </MemoryRouter>,
    )

  it('shows the typing indicator and blocks sending while a reply is pending', async () => {
    const user = userEvent.setup()
    renderChat(chat({ pending: true }))
    expect(screen.getByRole('status', { name: 'The tutor is writing' })).toBeInTheDocument()
    await user.type(screen.getByLabelText('Your message'), 'hola')
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  })

  it('pre-fills a "¿cómo se dice?" request with the cursor between the quotes', async () => {
    const user = userEvent.setup()
    renderChat(chat())
    await user.click(screen.getByRole('button', { name: '¿Cómo se dice…?' }))
    const field = screen.getByLabelText<HTMLTextAreaElement>('Your message')
    expect(field.value).toBe('¿Cómo se dice ""?')
    await waitFor(() => expect(field.selectionStart).toBe(15))
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled() // nothing asked yet
    await user.keyboard('dog') // straight away: the field already has focus
    expect(field.value).toBe('¿Cómo se dice "dog"?')
    expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled()
  })

  it("checks off today's words as the learner uses them", () => {
    renderChat(chat({ focus: ['regar', 'césped', 'planta'], used: ['yo', 'regar', 'planta'] }))
    expect(screen.getByRole('button', { name: 'Today’s words: 2 of 3 used' })).toBeInTheDocument()
    const words = screen.getByRole('list', { name: 'Today’s words' })
    expect(words).toHaveTextContent('✓ regar')
    expect(words).toHaveTextContent('✓ planta')
    expect(screen.getByText('césped')).toHaveTextContent('césped (not used yet)')
  })

  it('has no checklist when no words were taught first', () => {
    renderChat(chat())
    expect(screen.queryByRole('button', { name: /Today’s words/ })).not.toBeInTheDocument()
  })

  it('offers a new conversation once this one has ended', () => {
    renderChat(chat({ closed: true }))
    expect(screen.queryByLabelText('Your message')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Start a new one' })).toBeInTheDocument()
  })
})

describe('PreTaught', () => {
  it('says how many of the requested words were taught, and why not more', () => {
    render(<PreTaught lessons={[lesson]} requested={20} shortfall="The rest weren't really about the topic." />)
    expect(screen.getByText('1 of 20 words:')).toBeInTheDocument()
    expect(screen.getByText("The rest weren't really about the topic.")).toBeInTheDocument()
    expect(screen.getByText(/Try to use these in your replies/)).toBeInTheDocument()
  })

  it('has no shortfall note when every requested word was taught', () => {
    render(<PreTaught lessons={[lesson]} requested={1} shortfall={null} />)
    expect(screen.queryByText(/of 1 words/)).not.toBeInTheDocument()
  })
})

const summary = (overrides: Partial<Summary> = {}): Summary => ({
  minutes: 22,
  messages: 8,
  how_to_say: 3,
  corrections: 7,
  words_used: 55,
  words_taught: 14,
  first_time: ['celular', 'videojuego'],
  pre_taught: ['escena', 'pantalla', 'videojuego'],
  pre_taught_used: ['escena', 'videojuego'],
  went_well_en: 'You kept the conversation going.',
  work_on: ['Use estar for states: "estoy cansado".', 'Try "pantalla".'],
  notes_error: null,
  ...overrides,
})

describe('SummaryCard', () => {
  it('shows the numbers, new words, today’s words and the tutor’s notes', () => {
    render(<SummaryCard summary={summary()} />)
    const card = screen.getByRole('region', { name: 'Conversation summary' })
    expect(card).toHaveTextContent('Messages8')
    expect(card).toHaveTextContent('Corrections7')
    expect(screen.getByText('Used for the first time: 2 words')).toBeInTheDocument()
    expect(screen.getByText('celular, videojuego')).toBeInTheDocument()
    expect(screen.getByText('Today’s words: 2 of 3 used')).toBeInTheDocument()
    expect(screen.getByText('pantalla')).toHaveTextContent('pantalla (not used yet)')
    expect(screen.getByText('You kept the conversation going.')).toBeInTheDocument()
    expect(screen.getByText('Try "pantalla".')).toBeInTheDocument()
  })

  it('still shows the numbers when the notes failed', () => {
    render(<SummaryCard summary={summary({ went_well_en: null, work_on: [], notes_error: 'overloaded' })} />)
    expect(screen.getByText(/The tutor’s notes couldn’t be written \(overloaded\)/)).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Conversation summary' })).toHaveTextContent('Messages8')
  })
})

describe('ending a conversation', () => {
  const chat = (overrides: Partial<Chat> = {}): Chat => ({
    sessionId: 7,
    topic: 'los videojuegos',
    items: [{ kind: 'tutor', id: 1, turn: turn() }],
    taught: [],
    focus: [],
    used: [],
    pending: false,
    closed: false,
    ...overrides,
  })
  const renderChat = (value: Chat) =>
    render(
      <MemoryRouter>
        <ConversationsProvider>
          <ChatView chat={value} />
        </ConversationsProvider>
      </MemoryRouter>,
    )

  it('the Hasta luego button asks the server to end it', async () => {
    const user = userEvent.setup()
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ turn: turn({ reply_es: '¡Adiós!' }), summary: summary() })),
    )
    renderChat(chat())
    await user.click(screen.getByRole('button', { name: '¡Hasta luego!' }))
    expect(fetch).toHaveBeenCalledWith('/api/sessions/7/end', expect.objectContaining({ method: 'POST' }))
  })

  it('can’t be pressed while a reply is on its way', () => {
    renderChat(chat({ pending: true }))
    expect(screen.getByRole('button', { name: '¡Hasta luego!' })).toBeDisabled()
  })

  it('shows the summary in the chat once it has ended', () => {
    renderChat(chat({ closed: true, items: [{ kind: 'summary', id: 2, summary: summary() }] }))
    expect(screen.getByRole('region', { name: 'Conversation summary' })).toBeInTheDocument()
    expect(screen.getByText(/This conversation has ended/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '¡Hasta luego!' })).not.toBeInTheDocument()
  })
})

describe('api client', () => {
  it('turns an HTTP error into an ApiError with the server message', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: "This conversation isn't open." }), { status: 404 }),
    )
    const error = await api.send(9, 'hola').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 404, message: "This conversation isn't open." })
  })
})

describe('axis ticks', () => {
  it('always reach past the largest value', () => {
    // Regression: 1,008 words drew above an axis that stopped at 1,000.
    expect(niceTicks(693.5, 1008, 4)).toEqual([600, 700, 800, 900, 1000, 1100])
    expect(niceTicks(0, 100, 4)).toEqual([0, 25, 50, 75, 100])
  })
})

describe('growth chart data', () => {
  it('carries a mode forward through sessions where it did not change', () => {
    const points = toPoints([
      { session_id: null, topic: null, started_at: null, mode: 'production', words_added: 730, running_total: 730 },
      { session_id: null, topic: null, started_at: null, mode: 'recognition', words_added: 987, running_total: 987 },
      { session_id: 1, topic: 'el jardín', started_at: '2026-10-02 17:09:26', mode: 'production', words_added: 15, running_total: 745 },
    ])
    expect(points.map((p) => [p.label, p.totals.recognition, p.totals.production, p.added.recognition])).toEqual([
      ['Seed', 987, 730, 987],
      ['el jardín', 987, 745, 0],
    ])
  })
})

it('renders an accessible typing indicator', () => {
  render(<TypingIndicator />)
  expect(screen.getByRole('status')).toHaveTextContent('writing…')
})
