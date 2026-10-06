import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type CatalogItem, type Lesson, type ReadingState, type Recommendations } from '../api/client'
import { ReadingPage } from '../pages/ReadingPage'
import { ConversationsProvider } from '../state/conversations'
import { WhatNext } from '../pages/WhatNext'

afterEach(() => vi.restoreAllMocks())

const recommendations: Recommendations = {
  items: [
    { content_id: 1, kind: 'song', title: 'Fácil', author: null, new_words: 3, tokens: 100, unknown_share: 0.03, coverage: 0.97 },
    { content_id: 2, kind: 'story', title: 'Difícil', author: null, new_words: 8, tokens: 400, unknown_share: 0.09, coverage: 0.91 },
  ],
  books: [
    {
      book_id: 1,
      title: 'Cuentos',
      author: 'Horacio Quiroga',
      state: 'in progress',
      chapters: 18,
      density: 0.28,
      new_words: 4284,
      next_content_id: 12,
      next_chapter_no: 2,
      next_chapter_title: 'Los ojos sombríos',
      next_chapter_new_words: 323,
    },
  ],
  too_hard_items: [],
  too_hard_books: [],
  max_unknown_share: 0.1,
}

const catalog: CatalogItem[] = [
  {
    content_id: 1, kind: 'song', title: 'Fácil', author: null, book_id: null, book_title: null,
    chapter_no: null, indexed: true, new_words: 3, tokens: 100, coverage: 0.97, state: null,
  },
  {
    content_id: 30, kind: 'chapter', title: 'A la deriva', author: 'Horacio Quiroga', book_id: 1,
    book_title: 'Cuentos', chapter_no: 10, indexed: true, new_words: 253, tokens: 1009,
    coverage: 0.69, state: null,
  },
]

// What a started reading session returns; the page only needs its id.
const readingStarted = { session_id: 77 } as ReadingState

function renderWhatNext() {
  render(
    <MemoryRouter initialEntries={['/next']}>
      <Routes>
        <Route path="/next" element={<WhatNext />} />
        <Route path="/reading/:sessionId" element={<p>reader page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('What next?', () => {
  it('suggests the easiest song or story and a started book’s next chapter', async () => {
    vi.spyOn(api, 'recommend').mockResolvedValue(recommendations)
    vi.spyOn(api, 'catalog').mockResolvedValue(catalog)
    renderWhatNext()

    const song = await screen.findByRole('region', { name: 'Suggested song or story' })
    expect(song).toHaveTextContent('Fácil')
    expect(song).toHaveTextContent('3 new words · 97% of its words known')
    const book = screen.getByRole('region', { name: 'Suggested book' })
    expect(book).toHaveTextContent('Next: chapter 2, Los ojos sombríos · 323 new words')
    expect(within(book).getByRole('button', { name: 'Continue' })).toBeInTheDocument()
  })

  it('logs the default suggestion as recommended and opens the reader', async () => {
    vi.spyOn(api, 'recommend').mockResolvedValue(recommendations)
    vi.spyOn(api, 'catalog').mockResolvedValue(catalog)
    const start = vi.spyOn(api, 'readingStart').mockResolvedValue(readingStarted)
    renderWhatNext()

    const book = await screen.findByRole('region', { name: 'Suggested book' })
    await userEvent.click(within(book).getByRole('button', { name: 'Continue' }))

    expect(start).toHaveBeenCalledWith(12, 'recommended')
    expect(await screen.findByText('reader page')).toBeInTheDocument()
  })

  it('logs any other pick as the learner’s own request', async () => {
    vi.spyOn(api, 'recommend').mockResolvedValue(recommendations)
    vi.spyOn(api, 'catalog').mockResolvedValue(catalog)
    const start = vi.spyOn(api, 'readingStart').mockResolvedValue(readingStarted)
    renderWhatNext()

    await userEvent.click(await screen.findByRole('cell', { name: 'Difícil' }))
    expect(start).toHaveBeenLastCalledWith(2, 'requested')
  })

  it('opens a surprise pick as a recommendation', async () => {
    vi.spyOn(api, 'recommend').mockResolvedValue(recommendations)
    vi.spyOn(api, 'catalog').mockResolvedValue(catalog)
    vi.spyOn(api, 'surprise').mockResolvedValue({ content_id: 2, kind: 'story', title: 'Difícil', new_words: 8 })
    const start = vi.spyOn(api, 'readingStart').mockResolvedValue(readingStarted)
    renderWhatNext()

    await userEvent.click(await screen.findByRole('button', { name: 'Surprise me' }))
    await waitFor(() => expect(start).toHaveBeenCalledWith(2, 'recommended'))
  })

  it('lists items over the ceiling apart, and opens one as your own choice', async () => {
    const quiroga = { ...recommendations.books[0], state: 'new' as const, next_content_id: 1, next_chapter_no: 1 }
    vi.spyOn(api, 'recommend').mockResolvedValue({
      items: [],
      books: [],
      too_hard_items: [{ ...recommendations.items[1], unknown_share: 0.2, coverage: 0.8 }],
      too_hard_books: [quiroga],
      max_unknown_share: 0.1,
    })
    vi.spyOn(api, 'catalog').mockResolvedValue(catalog)
    const start = vi.spyOn(api, 'readingStart').mockResolvedValue(readingStarted)
    renderWhatNext()

    const table = await screen.findByRole('region', { name: 'Too hard for now' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((r) => r.textContent)).toEqual([
      'DifícilStory80% (needs 90%)8',
      'CuentosBook · next: chapter 172% (needs 90%)4,284',
    ])
    expect(screen.getByText(/None is within reach yet: each has more than 10% new words\. See/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Surprise me' })).toBeDisabled()
    await userEvent.click(within(table).getByRole('cell', { name: 'Difícil' }))
    expect(start).toHaveBeenCalledWith(2, 'requested')
  })

  it('explains how to add content when there is none', async () => {
    vi.spyOn(api, 'recommend').mockResolvedValue({ items: [], books: [], too_hard_items: [], too_hard_books: [], max_unknown_share: 0.1 })
    vi.spyOn(api, 'catalog').mockResolvedValue([])
    renderWhatNext()

    expect(await screen.findByText('Nothing to read yet')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Surprise me' })).not.toBeInTheDocument()
  })
})

const lessonFor = (lexeme_id: number, lemma: string): Lesson => ({
  lexeme_id,
  lemma,
  pos: 'NOUN',
  definition_en: `meaning of ${lemma}`,
  example: null,
  model_written: false,
})

const readingState = (overrides: Partial<ReadingState> = {}): ReadingState => ({
  session_id: 7,
  content_id: 30,
  kind: 'chapter',
  title: 'El cuento del pollo',
  author: 'E. S. Harrison',
  book_title: 'An Elementary Spanish Reader',
  chapter_no: 1,
  chapters: 21,
  paragraphs: [['Un día un pollo entra en un bosque.'], ['Una bellota cae en su cabeza.']],
  total_new: 3,
  remaining: 3,
  readable_until: 0,
  unstudied: ['bosque', 'bellota', 'cae'],
  finished: false,
  ...overrides,
})

function renderReading() {
  render(
    <ConversationsProvider>
      <MemoryRouter initialEntries={['/reading/7']}>
        <Routes>
          <Route path="/reading/:sessionId" element={<ReadingPage />} />
          <Route path="/chat/:sessionId" element={<p>chat page</p>} />
        </Routes>
      </MemoryRouter>
    </ConversationsProvider>,
  )
}

describe('Reading', () => {
  it('studies a batch of new words, in order, and then offers the next', async () => {
    vi.spyOn(api, 'reading').mockResolvedValue(readingState())
    const batch = vi
      .spyOn(api, 'readingBatch')
      .mockResolvedValueOnce({
        words: [
          { lexeme_id: 1, lesson: lessonFor(1, 'bosque'), context: 'Un día un pollo entra en un bosque.' },
          { lexeme_id: 2, lesson: lessonFor(2, 'bellota'), context: 'Una bellota cae en su cabeza.' },
        ],
        state: readingState(),
      })
      .mockResolvedValueOnce({
        words: [{ lexeme_id: 3, lesson: lessonFor(3, 'caer'), context: 'Una bellota cae en su cabeza.' }],
        state: readingState({ remaining: 1, readable_until: 1, unstudied: ['cae'] }),
      })
    const study = vi
      .spyOn(api, 'readingStudy')
      .mockResolvedValue(readingState({ remaining: 1, readable_until: 1, unstudied: ['cae'] }))
    renderReading()

    const words = await screen.findByRole('region', { name: 'Words to study' })
    expect(within(words).getByText('In the text: Un día un pollo entra en un bosque.')).toBeInTheDocument()
    expect(screen.getByText('Studied 0 of 3 new words')).toBeInTheDocument()
    await userEvent.click(within(words).getByRole('button', { name: 'I’ve studied these' }))

    expect(study).toHaveBeenCalledWith(7, [1, 2])
    expect(await screen.findByText('Studied 2 of 3 new words')).toBeInTheDocument()
    expect(await screen.findByText('meaning of caer')).toBeInTheDocument()
    expect(batch).toHaveBeenCalledTimes(2)
  })

  it('reads the text with unstudied words underlined and the studied part marked', async () => {
    const partly = readingState({ remaining: 1, readable_until: 1, unstudied: ['cae'] })
    vi.spyOn(api, 'reading').mockResolvedValue(partly)
    vi.spyOn(api, 'readingBatch').mockResolvedValue({ words: [], state: partly })
    const lookUp = vi.spyOn(api, 'readingLookUp').mockResolvedValue({
      lesson: lessonFor(3, 'caer'),
      state: readingState({ remaining: 0, readable_until: 2, unstudied: [] }),
    })
    renderReading()

    await userEvent.click(await screen.findByRole('tab', { name: 'Read' }))
    const text = screen.getByRole('region', { name: 'Text' })
    expect(within(text).getByRole('button', { name: 'cae' })).toHaveClass('decoration-accent')
    expect(within(text).getByRole('button', { name: 'bosque' })).not.toHaveClass('decoration-accent')
    expect(within(text).getByRole('separator')).toHaveTextContent('Every word above is studied')

    await userEvent.click(within(text).getByRole('button', { name: 'cae' }))
    expect(lookUp).toHaveBeenCalledWith(7, 'cae')
    expect(await within(text).findByText('meaning of caer')).toBeInTheDocument()
    expect(within(text).getByRole('button', { name: 'cae' })).not.toHaveClass('decoration-accent')
  })

  it('offers the talk only once finished, and opens it as a chat', async () => {
    vi.spyOn(api, 'reading').mockResolvedValue(readingState({ remaining: 0, unstudied: [], readable_until: 2 }))
    vi.spyOn(api, 'readingFinish').mockResolvedValue(
      readingState({ remaining: 0, unstudied: [], readable_until: 2, finished: true }),
    )
    const discuss = vi.spyOn(api, 'discuss').mockResolvedValue({
      session_id: 7,
      topic: '«El cuento del pollo»',
      lessons: [],
      requested_words: 0,
      shortfall: null,
      opening: {
        kind: 'conversation',
        reply_es: '¿Te gustó el cuento?',
        reply_en: null,
        note_en: null,
        lessons: [],
        not_words: [],
        used: [],
        pending: null,
      },
    })
    renderReading()

    // Every word studied: the page opens on the text.
    expect(await screen.findByRole('region', { name: 'Text' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Talk about it' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Finished' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Talk about it' }))
    expect(discuss).toHaveBeenCalledWith(7)
    expect(await screen.findByText('chat page')).toBeInTheDocument()
  })
})
