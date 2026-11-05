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
  } else if (url.pathname === '/api/staff/prebooking-estimate/contexts') {
    body = { contexts: [{ id: 'context-1', label: 'Example Clinic · location 1 · provider 1234567893 · POS 11' }] }
  } else if (url.pathname === '/api/staff/prebooking-estimate/plan') {
    body = { groups: [{ label: 'Office visit', selection_mode: 'single', codes: [{ code: '99213', reason: 'Possible office visit', role: 'consultation', service_type: '98' }] }], notes: [], message: 'Confirm the plan.' }
  } else if (url.pathname === '/api/staff/prebooking-estimate/check') {
    checks++
    assert.deepEqual(route.request().postDataJSON().selected_services, [{ code: '99213', units: 1 }])
    body = { coverage: { status: 'active_today', message: 'Synthetic coverage response.', coverage_start: null, coverage_end: null },
      payer: 'Example Health', checked_at: '2026-10-29T12:00:00Z', checked_on: '2026-10-29', check_ids: ['ec_synthetic'],
      patient_address: { line1: '100 Example Ave', line2: null, city: 'Albany', state: 'NY', postal_code: '12207', country: null },
      network_status: 'unknown', network_evidence: null, plan_name: null, benefits: null, lines: [],
      patient_min_cents: null, patient_max_cents: null, estimate_status: 'needs_review', issues: ['Verify network.'] }
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
  await page.getByLabel("Patient's birth date").fill('1980-01-01')
  await page.getByLabel('Insurance member ID').fill('SYNTHETIC')
  await page.getByLabel('Insurance company').fill('Example')
  await page.getByLabel('Reason for visit and anticipated care').fill('Planned office visit')
  assert.equal(checks, 0)
  await page.getByRole('option', { name: 'Example Health (EXAMPLE)' }).click()
  await page.getByLabel('Insurance member ID').focus()
  assert.equal(await page.getByLabel('Insurance member ID').evaluate((input) => document.activeElement === input), true)
  await page.getByRole('button', { name: 'Find possible services' }).click()
  await page.getByLabel(/99213.*Possible office visit/).check()
  await page.getByLabel('I reviewed these anticipated services and units with the clinician.').check()
  await page.getByRole('button', { name: 'Check benefits and estimate cost' }).click()
  assert.equal(checks, 1)
  await page.getByText('Network participation unverified').waitFor()
  await page.getByText('100 Example Ave').waitFor()
  console.log('PASS: staff confirm a prospective service before a synthetic payer check')
} finally {
  await browser.close()
}
