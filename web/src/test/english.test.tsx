import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type ReadingState, type Voice } from '../api/client'
import { allEnglish, markOf } from '../lib/marks'
import { ReadingPage } from '../pages/ReadingPage'
import { ConversationsProvider } from '../state/conversations'
import { SpeechProvider } from '../state/speech'
import type { AudioLike } from '../state/speechContext'

afterEach(() => vi.restoreAllMocks())

const song: ReadingState = {
  session_id: 7,
  content_id: 40,
  skill: 'lyrics',
  kind: 'song',
  title: 'Canción',
  author: 'Alguien',
  book_title: null,
  chapter_no: null,
  chapters: 0,
  paragraphs: [['Baila conmigo, so come with me', 'Come with me tonight'], ['Vamos a la party']],
  total_new: 0,
  remaining: 0,
  unstudied: [],
  readable_until: 3,
  marked: [
    { so: 'english', come: 'english', with: 'english', me: 'english' },
    { come: 'english', with: 'english', me: 'english', tonight: 'english' },
    { party: 'loanword' },
  ],
  is_private: true,
  finished: false,
}

const VOICES: Voice[] = [{ accent: 'mx', label: 'México', available: true }]

function renderSong(state: ReadingState = song) {
  vi.spyOn(api, 'reading').mockResolvedValue(state)
  const audio = { play: () => Promise.resolve(), pause: () => {}, src: '', onended: null, playbackRate: 1 }
  render(
    <SpeechProvider createAudio={() => audio as unknown as AudioLike} loadVoices={() => Promise.resolve(VOICES)}>
      <ConversationsProvider>
        <MemoryRouter initialEntries={['/reading/7']}>
          <Routes>
            <Route path="/reading/:sessionId" element={<ReadingPage />} />
          </Routes>
        </MemoryRouter>
      </ConversationsProvider>
    </SpeechProvider>,
  )
}

describe('English inside songs', () => {
  it('finds a word’s mark, including the pieces of a contraction', () => {
    expect(markOf('Come', { come: 'english' })).toBe('english')
    expect(markOf('don', { "don't": 'english' })).toBe('english')
    expect(markOf('party', { party: 'loanword' })).toBe('loanword')
    expect(markOf('come', undefined)).toBeUndefined()
    expect(allEnglish('Come with me!', { come: 'english', with: 'english', me: 'english' })).toBe(true)
    expect(allEnglish('Baila, come with me', { come: 'english', with: 'english', me: 'english' })).toBe(false)
  })

  it('shows English words in their own style, not clickable, and loanwords clickable', async () => {
    renderSong()
    await userEvent.click(await screen.findByRole('tab', { name: 'Read' }))
    const text = screen.getByRole('region', { name: 'Text' })
    const english = within(text).getAllByTitle('English')
    expect(english.map((e) => e.textContent)).toContain('come')
    expect(english.every((e) => e.tagName === 'SPAN' && e.className.includes('text-english'))).toBe(true)
    expect(within(text).getByRole('button', { name: 'Baila' })).toBeInTheDocument() // Spanish
    const party = within(text).getByRole('button', { name: 'party' })
    expect(party).toHaveAttribute('title', 'English loanword: look up “party”')
    expect(party.className).toContain('text-loanword')
    expect(within(text).getByLabelText('Marked words')).toHaveTextContent('English: the song in English')
    expect(within(text).getByLabelText('Marked words')).toHaveTextContent('loanword: English used as Spanish')
  })

  it('does not narrate a private song, though its words can still be heard', async () => {
    renderSong()
    await userEvent.click(await screen.findByRole('tab', { name: 'Read' }))
    expect(await screen.findByText(/Narration is off for copyrighted songs/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Read aloud/ })).not.toBeInTheDocument()
  })

  it('narrates a public song as before', async () => {
    renderSong({ ...song, is_private: false })
    await userEvent.click(await screen.findByRole('tab', { name: 'Read' }))
    expect(await screen.findAllByRole('button', { name: /Read aloud/ })).not.toHaveLength(0)
    expect(screen.queryByText(/Narration is off/)).not.toBeInTheDocument()
  })

  it('never offers an all-English line to translate', async () => {
    renderSong()
    await userEvent.click(await screen.findByRole('tab', { name: 'Try first' }))
    expect(screen.getByRole('textbox', { name: 'Your translation of line 1' })).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: /Come with me tonight/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Your translation of line 2' })).not.toBeInTheDocument()
    expect(screen.getByTitle('English: nothing to translate')).toHaveTextContent('Come with me tonight')
  })
})
