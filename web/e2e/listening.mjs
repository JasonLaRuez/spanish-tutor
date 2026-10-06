// Listening walk-through on a database copy, with the real Piper voices: narration of a
// book chapter (the highlighted sentence moves as each one ends), a clicked word, the
// accent toggle, and a conversation's opening read aloud. One model call (the opening),
// under 1¢. Needs the voices: uv run python -m spanish_tutor.speech download
import { chromium } from 'playwright-core'

const BASE = process.env.E2E_BASE ?? 'http://127.0.0.1:8765'
const OUT = process.argv[2] ?? '.'
const errors = []
const check = (ok, message) => {
  console.log(ok ? 'ok  ' : 'FAIL', message)
  if (!ok) process.exitCode = 1
}

const browser = await chromium.launch({ channel: 'msedge', headless: true })
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
page.on('pageerror', (e) => errors.push(String(e)))
// Every clip the page asks for: what was said, in which accent, and how the server answered.
const clips = []
page.on('response', async (response) => {
  const url = new URL(response.url())
  if (url.pathname !== '/api/speech') return
  clips.push({
    text: url.searchParams.get('text'),
    accent: url.searchParams.get('accent'),
    status: response.status(),
    type: response.headers()['content-type'],
  })
})
const clipsFor = (text) => clips.filter((clip) => clip.text === text)

// --- Narrating a chapter ------------------------------------------------------------------
await page.goto(`${BASE}/next`)
const book = page.getByRole('region', { name: 'Suggested book' })
await book.waitFor()
await book.getByRole('button', { name: /^(Start|Continue)$/ }).click()
await page.waitForURL(/\/reading\/\d+$/, { timeout: 60000 })
// The chapter may be fresh (study first), studied (read it now), or already open to read.
const words = page.getByRole('region', { name: 'Words to study' })
const readNow = page.getByRole('button', { name: 'Read it now' })
const readTab = page.getByRole('button', { name: 'Read', exact: true })
const text = page.getByRole('region', { name: 'Text' })
await words.or(readNow).or(text).or(readTab).first().waitFor({ timeout: 60000 })
if (await words.getByRole('button', { name: 'I’ve studied these' }).isVisible()) {
  await words.getByRole('button', { name: 'I’ve studied these' }).click()
  await page.getByText('Every new word is studied').waitFor({ timeout: 30000 })
}
if (await readNow.isVisible()) await readNow.click()
else if (!(await text.isVisible())) await readTab.click()
await text.waitFor()

const narration = page.getByRole('group', { name: 'Narration' })
check(await narration.isVisible(), 'the reader has a narration bar')
await narration.getByRole('button', { name: '▶ Read aloud' }).click()
const current = text.locator('[aria-current="true"]')
await current.waitFor()
const first = await current.innerText()
console.log('reading:', first)
await page.waitForFunction(
  (sentence) => document.querySelector('[aria-current="true"]')?.textContent !== sentence,
  first,
  { timeout: 30000 },
)
const second = await current.innerText()
console.log('then:   ', second)
check(second !== first, 'the highlight moves to the next sentence when one ends')
check(clipsFor(first)[0]?.status === 200 && clipsFor(first)[0]?.type === 'audio/wav', 'the sentence came back as audio/wav')
check(clipsFor(first)[0]?.accent === 'mx', 'in the default accent, México')
check(clipsFor(second).length > 0, 'the next sentence was asked for too (prefetched or played)')
await narration.getByText(/Reading sentence 2 of \d+/).waitFor()
await page.screenshot({ path: `${OUT}/listening-narration.png` })

// A word clicked mid-narration is said, and the narration pauses where it was.
const spoken = (word) =>
  page.waitForResponse((r) => new URL(r.url()).searchParams.get('text') === word, { timeout: 15000 })
const pollo = spoken('pollo')
await text.getByRole('button', { name: 'pollo', exact: true }).first().click()
check((await pollo).status() === 200, 'a clicked word is spoken')
await page.waitForFunction(() => document.body.innerText.includes('Paused at'))
check(await narration.getByRole('button', { name: '▶ Resume' }).isVisible(), 'the narration paused at its place')
await narration.getByRole('button', { name: '■ Stop' }).click()

// The accent toggle changes the voice for the next clip.
await page.getByRole('button', { name: 'Accent: México' }).click()
await page.getByRole('button', { name: 'Accent: España' }).waitFor()
const bosque = spoken('bosque')
await text.getByRole('button', { name: 'bosque', exact: true }).first().click()
const bosqueUrl = new URL((await bosque).url())
check(bosqueUrl.searchParams.get('accent') === 'es', 'after the toggle, words are said in the España voice')
await page.getByRole('button', { name: 'Accent: España' }).click() // back to México

// --- A conversation's opening, read aloud ---------------------------------------------------
await page.goto(`${BASE}/new`)
const firstClip = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/speech', { timeout: 90000 })
await page.getByRole('button', { name: 'Empezar' }).click() // open chat: no topic, no words
await page.waitForURL(/\/chat\/\d+$/, { timeout: 60000 })
const tutor = page.getByLabel('Tutor').first()
await tutor.waitFor({ timeout: 60000 })
const opening = (await tutor.locator('p.es').first().innerText()).trim()
console.log('opening:', opening)
const openingUrl = new URL((await firstClip).url())
check(
  openingUrl.searchParams.get('text') === opening && openingUrl.searchParams.get('accent') === 'mx',
  'the opening reply is read aloud on arrival',
)
check(await tutor.getByRole('button', { name: /Listen to the tutor|Stop/ }).isVisible(), 'the reply has its own listen button')
await page.screenshot({ path: `${OUT}/listening-chat.png` })

check(errors.length === 0, `no console errors${errors.length ? ': ' + errors.join(' | ') : ''}`)
console.log(`clips asked for: ${clips.length}`)
if (process.exitCode) for (const clip of clips) console.log('  ', JSON.stringify(clip))
await browser.close()
