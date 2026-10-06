import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router'
import { api, type Progress, type SessionSummary } from '../api/client'
import { formatNumber, when } from '../lib/text'
import { useConversations } from '../state/context'
import { SpeechSettings } from './SpeechSettings'

type Theme = 'system' | 'light' | 'dark'
const THEME_KEY = 'spanish-tutor-theme'

function readTheme(): Theme {
  try {
    const saved = localStorage.getItem(THEME_KEY)
    return saved === 'light' || saved === 'dark' ? saved : 'system'
  } catch {
    return 'system'
  }
}

function applyTheme(theme: Theme) {
  if (theme === 'system') document.documentElement.removeAttribute('data-theme')
  else document.documentElement.setAttribute('data-theme', theme)
  try {
    if (theme === 'system') localStorage.removeItem(THEME_KEY)
    else localStorage.setItem(THEME_KEY, theme)
  } catch {
    // storage unavailable: the choice lasts for this visit only
  }
}

const navClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center gap-2 rounded-md px-2.5 py-1.5 text-sm ${
    isActive ? 'bg-accent-soft font-medium text-ink' : 'text-ink-2 hover:bg-surface-2 hover:text-ink'
  }`

export function Layout() {
  const location = useLocation()
  const [theme, setTheme] = useState<Theme>(readTheme)
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [progress, setProgress] = useState<Progress | null>(null)

  useEffect(() => applyTheme(theme), [theme])

  // On a phone the sidebar is a drawer behind the menu button: closed on every page change
  // (it's open only on the page it was opened on), on Escape, and by tapping outside it.
  const [menuOpenOn, setMenuOpenOn] = useState<string | null>(null)
  const menuOpen = menuOpenOn === location.pathname
  const setMenuOpen = (open: boolean) => setMenuOpenOn(open ? location.pathname : null)
  useEffect(() => {
    if (!menuOpen) return
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && setMenuOpenOn(null)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [menuOpen])

  // Refresh the sidebar's history and counts on every page change, and when a conversation
  // ends (it stops being "open") or a reply changes the word counts.
  const { chats } = useConversations()
  const activity = Object.values(chats)
    .map((chat) => `${chat.sessionId}:${chat.items.length}:${chat.closed}`)
    .join(',')
  useEffect(() => {
    api.sessions().then(setSessions).catch(() => {})
    api.progress().then(setProgress).catch(() => {})
  }, [location.pathname, activity])

  const nextTheme: Record<Theme, Theme> = { system: 'light', light: 'dark', dark: 'system' }

  return (
    <div className="flex h-full flex-col md:flex-row">
      <header className="flex shrink-0 items-center gap-3 border-b border-line bg-surface px-3 py-2 md:hidden">
        <button
          type="button"
          onClick={() => setMenuOpen(true)}
          aria-label="Menu"
          aria-expanded={menuOpen}
          aria-controls="sidebar"
          className="grid h-10 w-10 place-items-center rounded-md text-xl text-ink hover:bg-surface-2"
        >
          <span aria-hidden>☰</span>
        </button>
        <NavLink to="/" className="flex items-center gap-2">
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-accent font-semibold text-accent-ink">ñ</span>
          <span className="font-semibold text-ink">Spanish Tutor</span>
        </NavLink>
      </header>

      {menuOpen && (
        <div className="fixed inset-0 z-30 bg-black/40 md:hidden" onClick={() => setMenuOpen(false)} aria-hidden />
      )}

      <aside
        id="sidebar"
        className={`fixed inset-y-0 left-0 z-40 flex w-64 shrink-0 flex-col border-r border-line bg-surface transition-transform md:static md:z-auto md:translate-x-0 ${
          menuOpen ? 'translate-x-0' : '-translate-x-full max-md:invisible'
        }`}
      >
        <div className="px-4 pt-5 pb-4">
          <NavLink to="/" className="flex items-center gap-2">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent text-lg font-semibold text-accent-ink">
              ñ
            </span>
            <span className="font-semibold text-ink">Spanish Tutor</span>
          </NavLink>
        </div>

        <nav className="space-y-0.5 px-2" aria-label="Main">
          <NavLink to="/" end className={navClass}>
            Home
          </NavLink>
          <NavLink to="/new" className={navClass}>
            Conversation
          </NavLink>
          <NavLink to="/history" className={navClass}>
            History
          </NavLink>
          <NavLink to="/progress" className={navClass}>
            Progress
          </NavLink>
          <NavLink to="/next" className={navClass}>
            What next?
          </NavLink>
          <NavLink to="/books" className={navClass}>
            Books
          </NavLink>
          <NavLink to="/songs" className={navClass}>
            Songs &amp; poems
          </NavLink>
          <NavLink to="/rate" className={navClass}>
            Rate
          </NavLink>
        </nav>

        <div className="mt-6 min-h-0 flex-1 overflow-y-auto px-2">
          <h2 className="px-2.5 pb-1 text-xs font-medium uppercase tracking-wide text-muted">
            Recent
          </h2>
          {sessions.length === 0 && <p className="px-2.5 text-sm text-muted">No conversations yet.</p>}
          {sessions.slice(0, 8).map((session) => (
            <NavLink
              key={session.session_id}
              to={session.active ? `/chat/${session.session_id}` : `/history/${session.session_id}`}
              className={navClass}
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate">{session.topic ?? 'Open conversation'}</span>
                <span className="block text-xs text-muted">
                  {when(session.started_at)}
                  {session.active ? ' · open' : ''}
                </span>
              </span>
            </NavLink>
          ))}
        </div>

        <div className="space-y-3 border-t border-line px-4 py-3">
          {progress && (
            <NavLink to="/progress" className="block text-sm" aria-label="Words you know">
              <span className="flex items-center gap-2 text-ink-2">
                <span className="h-2.5 w-2.5 rounded-full bg-recognize" aria-hidden />
                Recognize
                <span className="ml-auto font-medium text-ink">{formatNumber(progress.recognition)}</span>
              </span>
              <span className="mt-1 flex items-center gap-2 text-ink-2">
                <span className="h-2.5 w-2.5 rounded-full bg-produce" aria-hidden />
                Can produce
                <span className="ml-auto font-medium text-ink">{formatNumber(progress.production)}</span>
              </span>
            </NavLink>
          )}
          <SpeechSettings />
          <button
            type="button"
            onClick={() => setTheme(nextTheme[theme])}
            className="text-xs text-muted hover:text-ink"
          >
            Theme: {theme}
          </button>
        </div>
      </aside>

      <main className="min-h-0 min-w-0 flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
