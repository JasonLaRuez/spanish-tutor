// Practice words walk-through on a database copy: a topic conversation whose new words run
// short is filled with known words the learner hasn't used; using one ticks it off, and the
// summary counts it as used for the first time. Calls the model: the word choice, the
// opening, one reply, the goodbye and the notes, ~3-5¢.
//
// It needs a topic with few new words left. On a fresh copy, run first (from the repo root)
//   TUTOR_DB_PATH=copy.db uv run python <script> "la comida"
// with a script that marks all but 5 of the topic's new candidates as read (see the README).
import { chromium } from 'playwright-core'

const BASE = process.env.E2E_BASE ?? 'http://127.0.0.1:8765'
const OUT = process.argv[2] ?? '.'
const TOPIC = process.argv[3] ?? 'la comida'
const errors = []
const check = (ok, message) => {
  console.log(ok ? 'ok  ' : 'FAIL', message)
  if (!ok) process.exitCode = 1
}

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
page.on('pageerror', (e) => errors.push(String(e)))

await page.goto(`${BASE}/new`)
await page.getByLabel('¿De qué quieres hablar?').fill(TOPIC)
await page.locator('#new-words').focus()
await page.keyboard.press('End') // the slider's maximum: 20 words
check((await page.locator('#new-words').inputValue()) === '20', 'asked for 20 words')
await page.getByRole('button', { name: 'Empezar' }).click()

const practice = page.getByRole('region', { name: /Words to practice/ })
await practice.waitFor({ timeout: 120000 })
const fresh = page.getByRole('region', { name: 'Words for today' })
const newWords = (await fresh.count()) ? await fresh.locator('.es.font-semibold').allInnerTexts() : []
const practiceWords = await practice.locator('.es.font-semibold').allInnerTexts()
console.log('new:', newWords.join(', ') || '(none)')
console.log('practice:', practiceWords.join(', '))
check(practiceWords.length > 0, 'known words fill the gap in new words')
check(
  (await practice.getByText('practice', { exact: true }).count()) === practiceWords.length,
  'every practice card carries the practice tag',
)
check(newWords.length + practiceWords.length === 20 || (await page.getByText(/of 20 words/).isVisible()),
  '20 words in all, or a note saying why not')
await page.screenshot({ path: `${OUT}/conversation-practice.png`, fullPage: true })

// Use one practice word in a reply.
const preferred = ['desayuno', 'postre', 'pescado', 'arroz', 'almorzar', 'cenar', 'desayunar']
const word = preferred.find((w) => practiceWords.includes(w)) ?? practiceWords[0]
const message = /(ar|er|ir)$/.test(word) ? `Me gusta ${word} con mi familia.` : `Me gusta el ${word}.`
console.log('learner:', message)
await page.getByLabel('Your message').fill(message)
await page.getByRole('button', { name: 'Send' }).click()
const today = page.getByRole('button', { name: /Today’s words: 1 of \d+ used/ })
await today.waitFor({ timeout: 120000 }) // the checklist is open by default
const checklist = page.getByRole('list', { name: 'Today’s words' })
check((await checklist.innerText()).includes(`✓ ${word}`), `“${word}” is ticked off once used`)

await page.getByRole('button', { name: '¡Hasta luego!' }).click()
const summary = page.getByRole('region', { name: 'Conversation summary' })
await summary.waitFor({ timeout: 120000 })
const line = summary.getByText(/Words you knew but had never used/)
console.log('summary:', (await line.innerText()).replace(/\s+/g, ' '))
check(
  new RegExp(`1 of ${practiceWords.length} used for the first time \\(${word}\\)`).test(await line.innerText()),
  'the summary counts the practice word as used for the first time',
)
await page.screenshot({ path: `${OUT}/conversation-summary.png`, fullPage: true })

check(errors.length === 0, `no console errors${errors.length ? ': ' + errors.join(' | ') : ''}`)
await browser.close()
