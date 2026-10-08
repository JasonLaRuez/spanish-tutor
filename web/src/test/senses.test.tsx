import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type Lesson, type SenseEntry } from '../api/client'
import { LessonCard } from '../components/LessonCard'
import { SensesPage } from '../pages/Senses'

afterEach(() => vi.restoreAllMocks())

const entry = (
  sense_id: number,
  lemma: string,
  sense_en: string,
  register: SenseEntry['register'],
  extra: Partial<SenseEntry> = {},
): SenseEntry => ({
  sense_id, lexeme_id: sense_id < 3 ? 1 : sense_id, lemma, pos: 'NOUN', definition_en: `main ${lemma}`,
  sense_en, register, region: null, songs: 0, texts: 0, recognized: false, ...extra,
})

const entries: SenseEntry[] = [
  entry(1, 'lana', 'money', 'slang', { region: 'Mexico', songs: 3, recognized: true }),
  entry(2, 'lana', 'a fool', 'rare', { songs: 3, recognized: true }),
  entry(3, 'concha', 'female genitals', 'vulgar', { region: 'Argentina', texts: 2 }),
  entry(4, 'cuita', 'sorrow', 'archaic', { texts: 5 }),
]

describe('LessonCard', () => {
  const lesson: Lesson = {
    lexeme_id: 1, lemma: 'lana', pos: 'NOUN', definition_en: 'wool', example: null,
    model_written: false, practice: false, other_senses: [],
  }

  it('shows no other senses when a word has none', () => {
    render(<LessonCard lesson={lesson} />)
    expect(screen.queryByText('Other senses')).not.toBeInTheDocument()
  })

  it('lists other senses with their labels, a vulgar one marked', () => {
    render(
      <LessonCard
        lesson={{
          ...lesson,
          other_senses: [
            { sense_en: 'female genitals', register: 'vulgar', region: 'Argentina' },
            { sense_en: 'money', register: 'slang', region: 'Mexico' },
          ],
        }}
      />,
    )
    expect(screen.getByText('Other senses')).toBeInTheDocument()
    expect(screen.getByText('wool')).toBeInTheDocument()
    const vulgar = screen.getByText(/vulgar · Argentina/)
    expect(vulgar).toHaveAttribute('title', expect.stringContaining('best not to use'))
    expect(vulgar).toHaveTextContent('⚠')
    expect(screen.getByText(/slang · Mexico/)).not.toHaveTextContent('⚠')
  })
})

describe('SensesPage', () => {
  const show = () => {
    vi.spyOn(api, 'senses').mockResolvedValue(entries)
    render(<SensesPage />)
  }

  it('groups senses by word, with where the word appears', async () => {
    show()
    const lana = (await screen.findByText('lana')).closest('li') as HTMLElement
    expect(within(lana).getByText('money')).toBeInTheDocument()
    expect(within(lana).getByText('a fool')).toBeInTheDocument()
    expect(within(lana).getByText('3 songs')).toBeInTheDocument()
    expect(within(lana).getByText('· known')).toBeInTheDocument()
    expect(screen.getByText('3 words')).toBeInTheDocument()
  })

  it('filters by label, by where the word appears, by known words and by search', async () => {
    const user = userEvent.setup()
    show()
    await screen.findByText('lana')
    await user.click(screen.getByRole('button', { name: /archaic/ }))
    expect(screen.queryByText('cuita')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /archaic/ })).toHaveAttribute('aria-pressed', 'false')

    await user.selectOptions(screen.getByLabelText('Word appears'), 'songs')
    expect(screen.queryByText('concha')).not.toBeInTheDocument()
    expect(screen.getByText('lana')).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Word appears'), 'anywhere')
    await user.click(screen.getByLabelText('Only words I know'))
    expect(screen.getByText('1 word')).toBeInTheDocument()
    await user.click(screen.getByLabelText('Only words I know'))

    await user.type(screen.getByLabelText('Find a word or meaning'), 'genitals')
    expect(screen.getByText('concha')).toBeInTheDocument()
    expect(screen.queryByText('lana')).not.toBeInTheDocument()
  })

  it('explains how senses get here when there are none', async () => {
    vi.spyOn(api, 'senses').mockResolvedValue([])
    render(<SensesPage />)
    expect(await screen.findByText(/None yet/)).toBeInTheDocument()
  })
})
