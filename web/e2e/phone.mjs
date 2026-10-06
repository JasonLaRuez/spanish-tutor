// Phone-size walk-through on a database copy: every page at iPhone width (390x844) with
// nothing wider than the screen, the menu drawer opening and closing, and the desktop
// layout unchanged at 1280 px. Starts a reading session on the copy; no model calls.
import { chromium } from 'playwright-core'

const BASE = process.env.E2E_BASE ?? 'http://127.0.0.1:8765'
const OUT = process.argv[2] ?? '.'
const errors = []
const check = (ok, message) => {
  console.log(ok ? 'ok  ' : 'FAIL', message)
  if (!ok) process.exitCode = 1
}
const json = async (path, body) =>
  (await fetch(BASE + path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {})).json()

const book = (await json('/api/recommend')).books[0]
const reading = await json('/api/reading', { content_id: book.next_content_id, chosen_via: 'requested' })
const sessions = await json('/api/sessions')
const conversation = sessions.find((s) => s.skill === 'conversation')

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const phone = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })
phone.on('pageerror', (e) => errors.push(String(e)))

const pages = [
  ['/', 'home'], ['/new', 'new conversation'], ['/history', 'history'], ['/progress', 'progress'],
  ['/next', 'what next'], ['/books', 'books'], ['/songs', 'songs & poems'], ['/rate', 'rate'],
  [`/reading/${reading.session_id}`, 'reader'],
  ...(conversation ? [[`/history/${conversation.session_id}`, 'a transcript'], [`/chat/${conversation.session_id}`, 'a chat']] : []),
]
for (const [path, name] of pages) {
  await phone.goto(BASE + path)
  await phone.waitForTimeout(1500)
  // Wider than the screen: an element past the right edge that isn't inside its own
  // horizontally scrolling box (a table may scroll inside its frame).
  const wide = await phone.evaluate(() =>
    [...document.querySelectorAll('main *')]
      .filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1)
      .filter((el) => !el.closest('.overflow-x-auto'))
      .map((el) => el.tagName.toLowerCase()),
  )
  check(wide.length === 0, `${name}: nothing wider than the screen${wide.length ? ` (${wide.slice(0, 3).join(', ')})` : ''}`)
  await phone.screenshot({ path: `${OUT}/phone-${name.replaceAll(' ', '-')}.png` })
}

await phone.goto(BASE + '/')
const menu = phone.getByRole('button', { name: 'Menu' })
check(await menu.isVisible(), 'the phone layout has a menu button')
check(!(await phone.locator('#sidebar').isVisible()), 'the sidebar is hidden until the menu is opened')
await menu.click()
await phone.locator('#sidebar').getByRole('link', { name: 'Progress' }).waitFor()
check(await phone.locator('#sidebar').isVisible(), 'the menu opens the sidebar as a drawer')
await phone.screenshot({ path: `${OUT}/phone-menu.png` })
await phone.locator('#sidebar').getByRole('link', { name: 'Progress' }).click()
await phone.waitForURL(/\/progress$/)
await phone.waitForTimeout(400)
check(!(await phone.locator('#sidebar').isVisible()), 'choosing a page closes the drawer')

const desktop = await browser.newPage({ viewport: { width: 1280, height: 900 } })
await desktop.goto(BASE + '/')
check(await desktop.locator('#sidebar').isVisible(), 'on a desktop the sidebar is always shown')
check(!(await desktop.getByRole('button', { name: 'Menu' }).isVisible()), 'and there is no menu button')

check(errors.length === 0, `no page errors${errors.length ? ': ' + errors.join(' | ') : ''}`)
await browser.close()
