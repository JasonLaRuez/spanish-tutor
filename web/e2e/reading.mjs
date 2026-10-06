// Slice 4.2 walk-through on a database copy: study, read, finish (no model calls).
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

await page.goto(`${BASE}/next`)
const book = page.getByRole('region', { name: 'Suggested book' })
await book.waitFor()
console.log('suggested:', (await book.innerText()).replace(/\s+/g, ' '))
await book.getByRole('button', { name: 'Start' }).click()
await page.waitForURL(/\/reading\/\d+$/, { timeout: 60000 })
console.log('reading page:', await page.locator('h1').innerText())

const words = page.getByRole('region', { name: 'Words to study' })
await words.waitFor({ timeout: 60000 })
const progress = await page.getByRole('region', { name: 'Study progress' }).innerText()
console.log('progress:', progress.replace(/\s+/g, ' '))
const cards = await words.locator('.es.font-semibold').allInnerTexts()
console.log('batch:', cards.join(', '))
await page.screenshot({ path: `${OUT}/reading-study.png`, fullPage: true })
await words.getByRole('button', { name: 'I’ve studied these' }).click()
await page.getByText(/Studied (\d+) of \1 new words/).waitFor({ timeout: 30000 })
check(await page.getByText('Every new word is studied').isVisible(), 'after one batch every new word is studied (14 in chapter 1)')

await page.getByRole('button', { name: 'Read it now' }).click()
const text = page.getByRole('region', { name: 'Text' })
await text.waitFor()
check((await text.locator('.decoration-accent').count()) === 0, 'nothing left underlined')
await text.getByRole('button', { name: 'pollo' }).first().click()
await text.getByText(/Look up: pollo/).waitFor()
check(true, 'a word looks up from the text')
await page.emulateMedia({ colorScheme: 'dark' })
await page.screenshot({ path: `${OUT}/reading-read-dark.png` })
await page.emulateMedia({ colorScheme: 'light' })

await page.getByRole('button', { name: 'Finished' }).click()
const talk = page.getByRole('button', { name: 'Talk about it' })
await talk.waitFor()
check(await talk.isVisible(), 'Talk about it is offered after Finished (not pressed: it calls the model)')

await page.goto(`${BASE}/next`)
await book.waitFor()
const after = (await book.innerText()).replace(/\s+/g, ' ')
console.log('after:', after)
check(after.includes('Next: chapter 2'), 'the book moves on to chapter 2')

await page.goto(`${BASE}/books`)
const shelf = page.getByRole('region', { name: 'An Elementary Spanish Reader' })
await shelf.waitFor()
const shelfText = (await shelf.innerText()).replace(/\s+/g, ' ')
console.log('books:', shelfText.slice(0, 160))
check(shelfText.includes('1 of 21 chapters read') && shelfText.includes('Continue: chapter 2'), 'the Books page shows the progress and the next chapter')
await page.screenshot({ path: `${OUT}/books.png`, fullPage: true })

check(errors.length === 0, `no console errors${errors.length ? ': ' + errors.join(' / ') : ''}`)
await browser.close()
