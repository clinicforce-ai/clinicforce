// Synthetic browser workflow; every API call is intercepted.
import assert from 'node:assert/strict'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
page.setDefaultTimeout(10000)
const errors = [], lookups = [], appointments = []
let mode = 'suggestions', identityFailure = false
const candidate = { id: 'ecw-1', given_name: 'Sample', family_name: 'North Vale', birth_date: '1980-01-01', ecw_account_number: 'SYNTHETIC-001', differences: ['Last name'] }
const patient = { ...candidate, id: 'clinic-1', ecw_patient_id: candidate.id }
const intake = { id: 'intake-1', patient, fields: { ...patient }, source: 'ecw_lookup', documents: [], status: 'ready', created_at: '2026-10-08T12:00:00Z' }
page.on('pageerror', error => errors.push(error.message))
await page.route(url => url.pathname.startsWith('/api/'), async route => {
  const path = new URL(route.request().url()).pathname
  let body = {}
  if (path === '/api/staff/session') body = { staff: { id: 'staff-1', practice_id: 'clinic', practice_name: 'Synthetic Clinic', display_name: 'Clinician', role: 'staff' } }
  else if (path === '/api/staff/intakes/search') body = { intakes: [], has_more: false }
  else if (path === '/api/staff/patients/dashboard') body = { patients: [], counts: { all: 0 } }
  else if (path === '/api/staff/intakes/ecw-lookup') {
    const value = route.request().postDataJSON(); lookups.push(value)
    if (mode === 'timeout') return route.fulfill({ status: 502, json: { detail: 'eCW timed out. Retry the lookup.' } })
    if (value.selected_patient_id) {
      if (mode === 'stale') return route.fulfill({ status: 409, json: { detail: 'Patient details changed. Search again.' } })
      assert.equal(value.identity_confirmed, true)
      assert.equal(value.family_name, candidate.family_name)
      body = { ...intake, ecw_match: { patient: candidate, appointments: [] } }
    } else body = { status: 'selection_required', candidates: mode === 'empty' ? [] : value.ecw_account_number ? [candidate] : [candidate, { ...candidate, id: 'ecw-2', ecw_account_number: 'SYNTHETIC-002' }] }
  } else if (path === '/api/staff/intakes/intake-1') body = intake
  else if (path === '/api/doctor/insurance/commercial/appointments') {
    appointments.push(path)
    if (identityFailure) return route.fulfill({ status: 409, json: { detail: 'Confirm the eCW patient to continue.', code: 'ecw_patient_selection_required' } })
    body = { appointments: [] }
  } else if (path.endsWith('/cards')) body = { cards: [] }
  else if (path.endsWith('/checks')) body = { checks: [] }
  else throw new Error('Unexpected API request: ' + path)
  await route.fulfill({ json: body })
})
async function search() {
  await page.getByRole('button', { name: 'Open insurance estimator', exact: true }).click()
}
async function start() {
  await page.goto(process.env.UI_URL || 'http://127.0.0.1:5182/?staff')
  await page.getByRole('button', { name: /Pull patient from eCW/ }).click()
  await page.getByLabel('First name', { exact: true }).fill('Sample')
  await page.getByLabel('Last name', { exact: true }).fill('Vlae')
  await page.getByLabel('Date of birth', { exact: true }).fill('1980-01-01')
  await search()
}
try {
  await page.goto(process.env.UI_URL || 'http://127.0.0.1:5182/?staff')
  await page.getByRole('button', { name: /Pull patient from eCW/ }).click()
  await page.getByLabel('eCW account number (optional)').fill('SYNTHETIC-001')
  await page.getByLabel('Date of birth', { exact: true }).fill('1980-01-01')
  await search()
  const accountSelect = page.getByLabel('Similar eCW patients')
  await accountSelect.waitFor()
  assert.equal(lookups.at(-1).ecw_account_number, 'SYNTHETIC-001')
  assert.equal(lookups.at(-1).birth_date, '1980-01-01')
  assert.equal(lookups.at(-1).given_name, '')
  assert.equal(appointments.length, 0)
  await accountSelect.selectOption('ecw-1')
  await page.getByRole('checkbox', { name: /I confirmed this patient's/ }).check()
  await page.getByRole('button', { name: 'Use selected patient' }).click()
  await page.getByRole('heading', { name: 'Insurance estimator', exact: true }).waitFor()
  await page.getByRole('heading', { name: 'No upcoming visit returned' }).waitFor()
  assert.equal(lookups.at(-1).family_name, candidate.family_name)
  appointments.length = 0
  await start()
  const select = page.getByLabel('Similar eCW patients')
  const confirm = page.getByRole('checkbox', { name: /I confirmed this patient's/ })
  const use = page.getByRole('button', { name: 'Use selected patient' })
  await select.waitFor()
  assert.equal(await select.inputValue(), '')
  assert(await use.isDisabled())
  assert.equal(appointments.length, 0)
  await select.selectOption('ecw-1'); await confirm.check()
  await select.selectOption('ecw-2')
  assert(!await confirm.isChecked()); assert(await use.isDisabled())
  await page.getByLabel('Last name', { exact: true }).fill('Vale')
  assert.equal(await select.count(), 0)
  await search(); await select.waitFor()
  assert.equal(await select.inputValue(), '')
  await select.focus(); await page.keyboard.press('ArrowDown'); await page.keyboard.press('Enter')
  assert.equal(await select.inputValue(), 'ecw-1')
  await confirm.check()
  await page.setViewportSize({ width: 390, height: 844 })
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1))
  mode = 'stale'; await use.click()
  await page.getByRole('alert').filter({ hasText: 'Patient details changed' }).waitFor()
  assert.equal(appointments.length, 0)
  mode = 'suggestions'; await use.click()
  await page.getByRole('heading', { name: 'Insurance estimator', exact: true }).waitFor()
  await page.getByRole('heading', { name: 'No upcoming visit returned' }).waitFor()
  assert.equal(lookups.at(-1).selected_patient_id, 'ecw-1')
  assert.equal(lookups.at(-1).family_name, candidate.family_name)
  // A saved identity failure leads back to a prefilled lookup, not a retry loop.
  identityFailure = true
  await page.getByRole('button', { name: 'Refresh visits' }).first().click()
  await page.getByRole('button', { name: 'Find matching eCW patients' }).click()
  await page.getByRole('heading', { name: 'Find patient & estimate' }).waitFor()
  assert.equal(await page.getByLabel('Last name', { exact: true }).inputValue(), candidate.family_name)
  mode = 'empty'; await search()
  await page.getByRole('heading', { name: 'No close matches found' }).waitFor()
  assert.equal(await page.getByText(/The estimate could not be updated/).count(), 0)
  mode = 'timeout'; await search()
  await page.getByRole('alert').filter({ hasText: 'eCW timed out' }).waitFor()
  assert.deepEqual(errors, [])
  console.log('PASS: dropdown, explicit confirmation, keyboard/mobile, stale/no-match/timeout and estimator recovery')
} finally {
  await browser.close()
}
