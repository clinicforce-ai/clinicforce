// Synthetic browser regression; payer and check requests never leave the browser.
import assert from 'node:assert/strict'

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH })
const page = await browser.newPage()
let checks = 0

await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
  const url = new URL(route.request().url())
  let body
  if (url.pathname === '/api/staff/session') {
    body = { staff: { id: 'staff', practice_id: 'clinic', practice_name: 'Example Clinic', display_name: 'Clinician', role: 'admin' } }
  } else if (url.pathname === '/api/staff/patients/dashboard') {
    body = { patients: [], counts: { all: 0, accounts_with_cards: 0, accounts_without_cards: 0, card_only: 0, other: 0 } }
  } else if (url.pathname === '/api/staff/prebooking-eligibility/options') {
    body = { payers: [{ id: 'EXAMPLE', name: 'Example Health' }] }
  } else if (url.pathname === '/api/staff/prebooking-eligibility/check') {
    checks++
    body = { status: 'active_today', message: 'Synthetic coverage response.', checked_at: '2026-10-29T12:00:00Z', checked_on: '2026-10-29', payer: 'Example Health', check_id: null, coverage_start: null, coverage_end: null }
  } else if (url.pathname === '/api/staff/intakes/search') {
    body = { intakes: [], has_more: false }
  } else {
    throw new Error(`Unexpected API ${url.pathname}`)
  }
  await route.fulfill({ json: body })
})

try {
  await page.goto(process.env.UI_URL || 'http://127.0.0.1:5182/?staff')
  await page.getByRole('button', { name: 'Check before booking' }).click()
  await page.getByLabel("Patient's first name").fill('Pat')
  await page.getByLabel("Patient's last name").fill('Example')
  await page.getByLabel("Patient's date of birth").fill('1980-01-01')
  await page.getByLabel('Insurance member ID').fill('SYNTHETIC')
  await page.getByLabel('Insurance company').fill('Example')
  const submit = page.getByRole('button', { name: 'Check coverage today' })
  assert.equal(await submit.isDisabled(), true)
  assert.equal(checks, 0)
  await page.getByRole('option', { name: 'Example Health (EXAMPLE)' }).click()
  await page.getByLabel('Insurance member ID').focus()
  assert.equal(await page.getByLabel('Insurance member ID').evaluate((input) => document.activeElement === input), true)
  assert.equal(await submit.isEnabled(), true)
  await submit.click()
  assert.equal(checks, 1)
  console.log('PASS: selecting a visible payer enables a synthetic check')
} finally {
  await browser.close()
}
