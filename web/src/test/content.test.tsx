import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type CatalogItem, type ContentDetail, type Recommendations } from '../api/client'
import { Reader } from '../pages/Reader'
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

function renderWhatNext() {
  render(
    <MemoryRouter initialEntries={['/next']}>
      <Routes>
        <Route path="/next" element={<WhatNext />} />
        <Route path="/read/:contentId" element={<p>reader page</p>} />
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
    const start = vi.spyOn(api, 'startReading').mockResolvedValue({ ok: true })
    renderWhatNext()

    const book = await screen.findByRole('region', { name: 'Suggested book' })
    await userEvent.click(within(book).getByRole('button', { name: 'Continue' }))

    expect(start).toHaveBeenCalledWith(12, 'recommended')
    expect(await screen.findByText('reader page')).toBeInTheDocument()
  })

  it('logs any other pick as the learner’s own request', async () => {
    vi.spyOn(api, 'recommend').mockResolvedValue(recommendations)
    vi.spyOn(api, 'catalog').mockResolvedValue(catalog)
    const start = vi.spyOn(api, 'startReading').mockResolvedValue({ ok: true })
    renderWhatNext()

    await userEvent.click(await screen.findByRole('cell', { name: 'Difícil' }))
    expect(start).toHaveBeenLastCalledWith(2, 'requested')
  })

  it('opens a surprise pick as a recommendation', async () => {
    vi.spyOn(api, 'recommend').mockResolvedValue(recommendations)
    vi.spyOn(api, 'catalog').mockResolvedValue(catalog)
    vi.spyOn(api, 'surprise').mockResolvedValue({ content_id: 2, kind: 'story', title: 'Difícil', new_words: 8 })
    const start = vi.spyOn(api, 'startReading').mockResolvedValue({ ok: true })
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
    const start = vi.spyOn(api, 'startReading').mockResolvedValue({ ok: true })
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

const chapter = (overrides: Partial<ContentDetail> = {}): ContentDetail => ({
  content_id: 12,
  kind: 'chapter',
  title: 'Los ojos sombríos',
  author: 'Horacio Quiroga',
  source: 'gutenberg:13507',
  book_id: 1,
  book_title: 'Cuentos',
  chapter_no: 2,
  chapters: 18,
  tokens: 2092,
  unresolved_tokens: 0,
  indexed: true,
  started: true,
  finished: false,
  text_es: 'Primera línea\nsigue el párrafo.\n\n\nSegundo párrafo.',
  new_words: Array.from({ length: 25 }, (_, i) => ({
    lexeme_id: i + 1,
    lemma: i === 0 ? 'cocotaje' : `palabra${i}`,
    pos: 'NOUN',
    definition_en: `meaning ${i}`,
    occurrences: 25 - i,
    model_written: i === 0,
  })),
  ...overrides,
})

function renderReader() {
  render(
    <MemoryRouter initialEntries={['/read/12']}>
      <Routes>
        <Route path="/read/:contentId" element={<Reader />} />
        <Route path="/next" element={<p>what next page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Reader', () => {
  it('shows the new words first, then the text in paragraphs', async () => {
    vi.spyOn(api, 'content').mockResolvedValue(chapter())
    renderReader()

    expect(await screen.findByText('Cuentos · chapter 2 of 18 · Horacio Quiroga')).toBeInTheDocument()
    const words = screen.getByRole('region', { name: 'New words' })
    expect(within(words).getByText('25 new words')).toBeInTheDocument()
    expect(within(words).getByText('model-written')).toBeInTheDocument()
    expect(within(words).queryByText('palabra24')).not.toBeInTheDocument() // first 20 only
    await userEvent.click(within(words).getByRole('button', { name: 'Show all 25' }))
    expect(within(words).getByText('palabra24')).toBeInTheDocument()
    // A hard-wrapped line is joined into its paragraph.
    expect(screen.getByText('Primera línea sigue el párrafo.')).toBeInTheDocument()
    expect(screen.getByText('Segundo párrafo.')).toBeInTheDocument()
  })

  it('marks the item finished and returns to the suggestions', async () => {
    vi.spyOn(api, 'content').mockResolvedValue(chapter())
    const finish = vi.spyOn(api, 'finishReading').mockResolvedValue({ ok: true })
    renderReader()

    expect(await screen.findByText('Your next suggestion will be the next chapter.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Finished' }))
    expect(finish).toHaveBeenCalledWith(12)
    expect(await screen.findByText('what next page')).toBeInTheDocument()
  })

  it('says so when every word is known', async () => {
    vi.spyOn(api, 'content').mockResolvedValue(chapter({ new_words: [] }))
    renderReader()
    expect(await screen.findByText('You know every word in this one.')).toBeInTheDocument()
  })
})
