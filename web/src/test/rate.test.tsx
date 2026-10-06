import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type EvalOverview, type RatingItem, type RatingQueue } from '../api/client'
import { RatePage } from '../pages/Rate'

const word = (overrides: Partial<RatingItem> = {}): RatingItem => ({
  item_id: 1,
  item_type: 'new_word_flag',
  source_ref: 'benchmark:r:turn:63:lexeme:2400',
  content: { lemma: 'qué', pos: 'PRON', definition_en: 'what', sentence: '¡Qué bien!', topic: 'la comida' },
  score: null,
  label: null,
  rated_at: null,
  ...overrides,
})

const queue = (items: RatingItem[]): RatingQueue => ({
  criterion: {
    item_type: 'new_word_flag',
    name: 'truly_new',
    question: 'Did you already know this word, as it’s used in the sentence?',
    labels: ['new', 'known', 'not_a_word'],
    scale: null,
  },
  items,
})

const rate = (k: number, n: number) => ({ k, n, value: n ? k / n : null, low: n ? 0.1 : null, high: n ? 0.9 : null })
const overview = (): EvalOverview => ({
  progress: { new_word_flag: { items: 2, rated: 1 }, translation_line: { items: 0, rated: 0 } },
  new_word_precision: { all: rate(1, 2), real: rate(1, 1), benchmark: rate(0, 1) },
  new_word_labels: { new: 1, known: 1, not_a_word: 0 },
  false_flags: ['como'],
})

afterEach(() => vi.restoreAllMocks())

describe('The rating page', () => {
  it('shows the next unrated word with its sentence and the choices', async () => {
    vi.spyOn(api, 'evalOverview').mockResolvedValue(overview())
    vi.spyOn(api, 'ratingQueue').mockResolvedValue(queue([word(), word({ item_id: 2, label: 'known', content: { lemma: 'como' } })]))
    render(<RatePage />)
    expect(await screen.findByText('¡Qué bien!')).toBeInTheDocument()
    expect(screen.getByText('qué')).toBeInTheDocument()
    expect(screen.getByText('Benchmark · la comida')).toBeInTheDocument()
    const card = screen.getByRole('region', { name: 'To rate' })
    expect(within(card).getByRole('button', { name: 'New to me' })).toHaveAttribute('aria-keyshortcuts', '1')
    expect(within(card).getByRole('button', { name: 'Not a real word' })).toHaveAttribute('aria-keyshortcuts', '3')
    expect(screen.getByText('1 left to rate')).toBeInTheDocument()
    const rated = screen.getByRole('region', { name: 'Rated' })
    expect(rated).toHaveTextContent('como')
    const tab = await screen.findByRole('tab', { name: /^New words/ })
    expect(tab).toHaveAttribute('aria-selected', 'true')
    await waitFor(() => expect(tab).toHaveTextContent('New words 1/2'))
  })

  it('rates with a click or a number key, then reloads', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'evalOverview').mockResolvedValue(overview())
    const load = vi.spyOn(api, 'ratingQueue').mockResolvedValue(queue([word()]))
    const send = vi.spyOn(api, 'rate').mockResolvedValue({ ok: true })
    render(<RatePage />)
    await user.click(await screen.findByRole('button', { name: 'New to me' }))
    expect(send).toHaveBeenCalledWith(1, { label: 'new' })
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2))
    await user.keyboard('2')
    expect(send).toHaveBeenLastCalledWith(1, { label: 'known' })
  })

  it('a rated word can be changed from the list', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'evalOverview').mockResolvedValue(overview())
    vi.spyOn(api, 'ratingQueue').mockResolvedValue(queue([word({ label: 'known' })]))
    const send = vi.spyOn(api, 'rate').mockResolvedValue({ ok: true })
    render(<RatePage />)
    expect(await screen.findByText(/Everything here is rated/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'I knew it' })).toHaveAttribute('aria-pressed', 'true')
    await user.click(screen.getByRole('button', { name: 'New to me' }))
    expect(send).toHaveBeenCalledWith(1, { label: 'new' })
  })

  it('shows precision with its interval, or says nothing is rated yet', async () => {
    vi.spyOn(api, 'evalOverview').mockResolvedValue({
      ...overview(),
      new_word_precision: { all: rate(1, 2), real: rate(1, 1), benchmark: rate(0, 0) },
    })
    vi.spyOn(api, 'ratingQueue').mockResolvedValue(queue([]))
    render(<RatePage />)
    expect(await screen.findByText('1 of 2 · 95% CI 10%–90%')).toBeInTheDocument()
    expect(screen.getByText('nothing rated yet')).toBeInTheDocument()
    expect(screen.getByText('Nothing to rate yet.')).toBeInTheDocument()
  })

  it('scores translation lines from 1 to 5', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'evalOverview').mockResolvedValue(overview())
    vi.spyOn(api, 'ratingQueue').mockResolvedValue({
      criterion: { item_type: 'translation_line', name: 'naturalness', question: 'How natural?', labels: [], scale: [1, 5] },
      items: [word({ item_type: 'translation_line', content: { natural_en: 'For one glance, a world;' } })],
    })
    const send = vi.spyOn(api, 'rate').mockResolvedValue({ ok: true })
    render(<RatePage />)
    expect(await screen.findByText('For one glance, a world;')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '4' }))
    expect(send).toHaveBeenCalledWith(1, { score: 4 })
  })
})
