// Slice 4.3 walk-through on a database copy: a poem from the library, study, try, compare
// (one real translation and one real comparison, ~2 cents), finish.
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

await page.goto(`${BASE}/songs`)
const row = page.getByRole('row', { name: /Rima XXIII/ })
await row.waitFor()
console.log('library row:', (await row.innerText()).replace(/\s+/g, ' '))
await page.screenshot({ path: `${OUT}/songs.png`, fullPage: false })
await row.click()
await page.waitForURL(/\/reading\/\d+$/, { timeout: 60000 })
check((await page.locator('h1').innerText()) === 'Rima XXIII', 'the poem opens')
const tabs = await page.getByRole('tab').allInnerTexts()
check(tabs.join('|').replace(/ \(\d+\)/, '') === 'Study|Try first|Compare|Read', `four steps: ${tabs.join(', ')}`)

const words = page.getByRole('region', { name: 'Words to study' })
await words.waitFor({ timeout: 60000 })
console.log('to study:', (await words.locator('.es.font-semibold').allInnerTexts()).join(', '))
await words.getByRole('button', { name: 'I’ve studied these' }).click()
await page.getByRole('button', { name: 'Try translating' }).click()

const first = page.getByRole('textbox', { name: 'Your translation of line 1' })
await first.fill('For a look, a world')
await page.getByRole('button', { name: 'Compare' }).click()
const compared = page.getByRole('region', { name: 'Compare' })
await compared.waitFor({ timeout: 120000 })
const firstLine = compared.locator('li').first()
const text = (await firstLine.innerText()).replace(/\s+/g, ' ')
console.log('line 1:', text)
check(/(Right|Close|Missed)/.test(text), 'the attempt has a verdict') // the badge follows the text directly
check(text.includes('NATURAL') || text.includes('Natural'), 'natural and literal translations are shown')
console.log('lines compared:', await compared.locator('li').count())
await page.screenshot({ path: `${OUT}/lyrics-compare.png`, fullPage: true })

await page.getByRole('button', { name: 'Finished' }).click()
await page.getByRole('button', { name: 'Talk about it' }).waitFor()
check(true, 'Talk about it is offered after Finished (not pressed)')

await page.emulateMedia({ colorScheme: 'dark' })
await page.goto(`${BASE}/songs`)
await row.waitFor()
check((await row.innerText()).includes('Finished'), 'the library shows it finished')
await page.screenshot({ path: `${OUT}/songs-dark.png` })

check(errors.length === 0, `no console errors${errors.length ? ': ' + errors.join(' / ') : ''}`)
await browser.close()
