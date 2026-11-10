// Synthetic browser regression; payer and check requests never leave the browser.
import assert from 'node:assert/strict'

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH })
const page = await browser.newPage()
let checks = 0
let priced = false

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
    body = priced ? { coverage: { status: 'active_today', message: 'Synthetic coverage response.', coverage_start: null, coverage_end: null },
      payer: 'Example Health', checked_at: '2026-10-29T12:00:00Z', checked_on: '2026-10-29', check_ids: ['ec_synthetic'],
      network_status: 'in_network', network_evidence: { source: 'clinic_library_contract', reason: 'Synthetic NMCT contract.' }, plan_name: 'Example PPO',
      benefits: { copay_cents: 2500, coinsurance_percent: '0', deductible_annual_cents: 100000, deductible_remaining_cents: 40000,
        oop_annual_cents: 300000, oop_remaining_cents: 120000 },
      lines: [{ code: '99213', units: 1, role: 'visit', service_type: '98', allowed_cents: 10000, patient_cents: 2500,
        rate_source: 'Synthetic signed rate', benefits: { copay_cents: 2500 }, issues: [], conditions: [] }],
      allowed_total_cents: 10000, plan_total_cents: 7500,
      patient_min_cents: 2500, patient_max_cents: 2500, estimate_status: 'priced', issues: [] }
      : { coverage: { status: 'active_today', message: 'Synthetic coverage response.', coverage_start: null, coverage_end: null },
        payer: 'Example Health', checked_at: '2026-10-29T12:00:00Z', checked_on: '2026-10-29', check_ids: ['ec_synthetic'],
        network_status: 'unknown', network_evidence: { reason: 'Contract scope unresolved.' }, plan_name: null, benefits: null,
        lines: [{ code: '99213', units: 1, role: 'visit', service_type: '98', allowed_cents: null, patient_cents: null,
          rate_source: null, benefits: {}, issues: ['Verify contract rate.'], conditions: [] }],
        allowed_total_cents: null, plan_total_cents: null,
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
  await page.getByText('Contract scope unresolved.').waitFor()
  assert.match(await page.locator('.prebooking-cost-grid').innerText(), /Needs review/)
  assert.match(await page.locator('.prebooking-service-row').innerText(), /Verify contract rate/)

  priced = true
  await page.getByLabel('Insurance member ID').fill('SYNTHETIC2')
  await page.getByRole('button', { name: 'Check benefits and estimate cost' }).click()
  assert.equal(checks, 2)
  await page.getByText('Synthetic NMCT contract.').waitFor()
  const totals = await page.locator('.prebooking-cost-grid').innerText()
  assert.match(totals, /\$100\.00/)
  assert.match(totals, /\$25\.00/)
  assert.match(await page.locator('.prebooking-share-labels').innerText(), /\$75\.00/)
  assert.match(await page.locator('.prebooking-service-row').innerText(), /Synthetic signed rate/)
  assert.equal(await page.getByText('Deductible remaining').count(), 1)
  await page.setViewportSize({ width: 390, height: 844 })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true)
  console.log('PASS: prebooking review and priced totals reconcile in responsive staff UI')
} finally {
  await browser.close()
}
