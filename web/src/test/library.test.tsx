import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type CatalogItem, type Recommendations } from '../api/client'
import { Songs } from '../pages/Songs'
import { Stories } from '../pages/Stories'

afterEach(() => vi.restoreAllMocks())

const item = (
  content_id: number,
  title: string,
  collection: string | null,
  new_words: number,
  coverage: number,
  kind: CatalogItem['kind'] = 'story',
  author: string | null = 'Martí',
): CatalogItem => ({
  content_id, kind, title, author, book_id: null, book_title: null, chapter_no: null, collection,
  indexed: true, new_words, tokens: 500, coverage, state: null,
})

const catalog: CatalogItem[] = [
  item(1, 'Tres héroes', 'La Edad de Oro', 40, 0.7),
  item(2, 'Meñique', 'La Edad de Oro', 9, 0.95),
  item(3, 'Platero', 'Platero y yo', 3, 0.97, 'story', 'Jiménez'),
  item(4, 'Un cuento suelto', null, 12, 0.7, 'story', 'Alguien'),
  item(5, 'Rima I', 'Rimas', 2, 0.9, 'poem', 'Bécquer'),
  item(6, 'Un capítulo', null, 1, 0.99, 'chapter'),
]

const recommendations = { max_unknown_share: 0.2 } as Recommendations

function show(page: React.ReactElement, path: string) {
  vi.spyOn(api, 'catalog').mockResolvedValue(catalog)
  vi.spyOn(api, 'recommend').mockResolvedValue(recommendations)
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={path} element={page} />
        <Route path="/reading/:sessionId" element={<p>Reading page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('The stories library', () => {
  it('groups stories by collection, the one with the easiest story first', async () => {
    show(<Stories />, '/stories')
    await screen.findByText('Platero y yo')
    const groups = screen.getAllByRole('group').map((g) => within(g).getAllByText(/./)[0].textContent)
    expect(groups).toEqual(['Platero y yo', 'La Edad de Oro', 'Single stories'])
    expect(screen.queryByText('Rima I')).not.toBeInTheDocument() // poems live on Songs & poems
    expect(screen.queryByText('Un capítulo')).not.toBeInTheDocument() // chapters on Books
  })

  it('lists a collection fewest new words first and says how many are within reach', async () => {
    show(<Stories />, '/stories')
    const edad = (await screen.findByText('La Edad de Oro')).closest('details')!
    expect(edad).toHaveTextContent('2 · 1 within reach')
    const rows = within(edad).getAllByRole('row').slice(1)
    expect(rows.map((r) => within(r).getAllByRole('cell')[0].textContent)).toEqual(['Meñique', 'Tres héroes'])
    expect(rows[1]).toHaveTextContent('(too hard for now)')
  })

  it('can show only what is within reach', async () => {
    const user = userEvent.setup()
    show(<Stories />, '/stories')
    await screen.findByText('La Edad de Oro')
    await user.click(screen.getByLabelText(/Only what’s within reach/))
    expect(screen.queryByText('Tres héroes')).not.toBeInTheDocument()
    expect(screen.queryByText('Single stories')).not.toBeInTheDocument() // nothing left in it
    expect(screen.getByText('Meñique')).toBeInTheDocument()
  })

  it('opens a story as a requested reading session', async () => {
    const user = userEvent.setup()
    const start = vi.spyOn(api, 'readingStart').mockResolvedValue({ session_id: 7 } as never)
    show(<Stories />, '/stories')
    await user.click(await screen.findByText('Platero y yo'))
    await user.click(screen.getByText('Platero'))
    await waitFor(() => expect(start).toHaveBeenCalledWith(3, 'requested'))
    expect(await screen.findByText('Reading page')).toBeInTheDocument()
  })
})

describe('The songs and poems library', () => {
  it('shows poems grouped by collection with their kind', async () => {
    show(<Songs />, '/songs')
    const rimas = (await screen.findByText('Rimas')).closest('details')!
    expect(rimas).toHaveAttribute('open') // the only group starts open
    expect(within(rimas).getByText('poem')).toBeInTheDocument()
    expect(screen.queryByText('Meñique')).not.toBeInTheDocument()
  })
})
