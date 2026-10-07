// Run with a local Vite server and an installed Playwright browser:
// PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node testing/ecw_candidate_ui_check.mjs
import assert from 'node:assert/strict'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
const errors = []
page.on('pageerror', error => errors.push(error.message))
const requests = []
const clinicSearches = []
const candidates = ['Mary Ann', 'Marly Ann'].map((given_name, i) => ({ id: `synthetic-${i}`, given_name, family_name: 'Example', birth_date: '1980-01-15', ecw_account_number: `SYNTHETIC-${i}`, match_kind: 'similar_first_name' }))
let stale = false
await page.route('**/api/**', async route => {
  const path = new URL(route.request().url()).pathname
  if (!path.startsWith('/api/')) return route.continue()
  let body = {}; let status = 200
  if (path.endsWith('/session')) body = { staff: { id: 'staff-1', role: 'staff', practice_id: 'practice-1', practice_name: 'Synthetic clinic', display_name: 'Synthetic clinician' } }
  else if (path.endsWith('/intakes/search')) body = { intakes: [], has_more: false, next_offset: null }
  else if (path.endsWith('/patients/dashboard')) body = { patients: [], counts: { all: 0, accounts_with_cards: 0, accounts_without_cards: 0, card_only: 0, other: 0 } }
  else if (path.endsWith('/ecw-lookup')) {
    const input = route.request().postDataJSON(); requests.push(input)
    if (!input.candidate_id) body = { requires_confirmation: true, candidates }
    else if (stale) { status = 409; body = { detail: 'The selected eCW patient no longer matches this search. Search again and review the details.' } }
    else { status = 201; body = { id: 'intake-1', source: 'ecw_lookup', documents: [], fields: { given_name: 'Mary Ann', family_name: 'Example', birth_date: '1980-01-15', ecw_patient_id: 'synthetic-0' }, ecw_match: { patient: candidates[0], appointments: [{ id: 'visit-1', start: '2026-10-08T09:00:00-04:00' }] } } }
  } else if (path.endsWith('/patients/search')) {
    clinicSearches.push(route.request().postDataJSON())
    body = { patients: [{ id: 'local-1', given_name: 'Mary Ann', family_name: 'Example', birth_date: '1980-01-15' }, { id: 'local-2', given_name: 'Mary Ann', family_name: 'Example', birth_date: '1980-01-15' }] }
  }
  else throw new Error(`Unexpected API call: ${path}`)
  await route.fulfill({ status, contentType: 'application/json', headers: { 'Cache-Control': 'no-store' }, body: JSON.stringify(body) })
})
try {
  await page.goto(process.env.CLINICFORCE_UI_URL || 'http://127.0.0.1:5187/?staff')
  await page.getByRole('button', { name: /Pull patient from eCW/i }).click()
  await page.getByLabel('First name', { exact: true }).fill('Marry Ann')
  await page.getByLabel('Last name', { exact: true }).fill('Example')
  await page.getByLabel('Date of birth', { exact: true }).fill('1980-01-15')
  const search = page.getByRole('button', { name: 'Open insurance estimator', exact: true })
  await search.click()
  const heading = page.getByRole('heading', { name: 'Confirm the eCW patient' })
  await heading.waitFor()
  assert.equal(await heading.evaluate(el => document.activeElement === el), true)
  const confirm = page.getByRole('button', { name: 'Confirm patient and continue' })
  const acknowledgement = page.getByRole('checkbox', { name: /I verified/ })
  assert.equal(await confirm.isDisabled(), true)
  assert.equal(await page.getByRole('radio').first().isChecked(), false)
  await page.getByRole('radio').first().check()
  assert.equal(await confirm.isDisabled(), true)
  await acknowledgement.check()
  assert.equal(await confirm.isEnabled(), true)
  await page.getByRole('radio').nth(1).check()
  assert.equal(await acknowledgement.isChecked(), false)
  assert.equal(await confirm.isDisabled(), true)
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true)
  await page.getByRole('button', { name: /None of these/ }).click()
  assert.equal(await heading.count(), 0)
  assert.equal(requests.length, 1)
  await search.click(); await heading.waitFor()
  await page.getByLabel('First name', { exact: true }).fill('Marry Anne')
  assert.equal(await heading.count(), 0)
  await page.getByLabel('First name', { exact: true }).fill('Marry Ann')
  await search.click(); await heading.waitFor()
  stale = true
  await page.getByRole('radio').first().check(); await acknowledgement.check(); await confirm.click()
  await page.getByText(/no longer matches this search/).waitFor()
  assert.equal(await heading.count(), 0)
  stale = false
  await search.click(); await heading.waitFor()
  await page.getByRole('radio').first().focus(); await page.keyboard.press('Space')
  await acknowledgement.focus(); await page.keyboard.press('Space')
  await confirm.focus(); await page.keyboard.press('Enter')
  await page.getByRole('heading', { name: 'eCW patient found', exact: true }).waitFor()
  assert.deepEqual(clinicSearches, [{ given_name: 'Mary Ann', family_name: 'Example', birth_date: '1980-01-15' }])
  const submitted = requests.filter(row => row.candidate_id)
  assert.equal(submitted.length, 2)
  assert.deepEqual(submitted[1], { given_name: 'Marry Ann', middle_initial: '', family_name: 'Example', birth_date: '1980-01-15', candidate_id: 'synthetic-0', candidate_given_name: 'Mary Ann', identity_confirmed: true })
  assert.deepEqual(errors, [])
  console.log('PASS: candidate review, no preselection, explicit confirmation, cancellation, edit reset, stale selection, keyboard controls, mobile overflow and confirmation payload.')
} finally { await browser.close() }
