import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { Layout } from '../components/Layout'
import { ConversationsProvider } from '../state/conversations'

function renderLayout() {
  return render(
    <ConversationsProvider>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<p>Home page</p>} />
            <Route path="progress" element={<p>Progress page</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ConversationsProvider>,
  )
}

beforeEach(() => {
  vi.spyOn(api, 'sessions').mockResolvedValue([])
  vi.spyOn(api, 'progress').mockRejectedValue(new Error('not needed'))
})
afterEach(() => vi.restoreAllMocks())

describe('The phone menu (the sidebar as a drawer)', () => {
  it('opens from the menu button', async () => {
    const user = userEvent.setup()
    renderLayout()
    const menu = screen.getByRole('button', { name: 'Menu' })
    expect(menu).toHaveAttribute('aria-expanded', 'false')
    await user.click(menu)
    expect(menu).toHaveAttribute('aria-expanded', 'true')
    expect(document.getElementById('sidebar')?.className).toContain('translate-x-0')
  })

  it('closes when a page is chosen', async () => {
    const user = userEvent.setup()
    renderLayout()
    await user.click(screen.getByRole('button', { name: 'Menu' }))
    await user.click(screen.getByRole('link', { name: 'Progress' }))
    expect(screen.getByText('Progress page')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Menu' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('closes on Escape and on a tap outside it', async () => {
    const user = userEvent.setup()
    renderLayout()
    const menu = screen.getByRole('button', { name: 'Menu' })
    await user.click(menu)
    await user.keyboard('{Escape}')
    expect(menu).toHaveAttribute('aria-expanded', 'false')
    await user.click(menu)
    const backdrop = document.querySelector('.bg-black\\/40') as HTMLElement
    await user.click(backdrop)
    expect(menu).toHaveAttribute('aria-expanded', 'false')
  })

  it('is hidden from screen readers and the tab order while closed (on a phone)', () => {
    renderLayout()
    expect(document.getElementById('sidebar')?.className).toContain('max-md:invisible')
  })
})
