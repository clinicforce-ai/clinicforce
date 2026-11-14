// Synthetic browser regression; payer and check requests never leave the browser.
import assert from 'node:assert/strict'

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH })
const page = await browser.newPage()
let checks = 0
let plans = 0

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
    plans++
    body = route.request().postDataJSON().reason.includes('5 nerve conduction studies')
      ? { groups: [{ label: 'Planned nerve testing', description: 'Five studies were documented.', selection_mode: 'single', codes: [{ code: '95909', label: 'Nerve conduction, five or six studies', reason: 'The clinician described five studies.', role: 'diagnostic_test', service_type: '73' }] }], notes: [], message: 'Review possible care.' }
      : { groups: [{ label: 'Possible nerve testing', description: 'The clinician may consider testing; the study count is unknown.', selection_mode: 'single', codes: [] }], notes: ['Ask if testing is planned and how many studies.'], message: 'Review possible care.' }
  } else if (url.pathname === '/api/staff/prebooking-estimate/check') {
    checks++
    assert.deepEqual(route.request().postDataJSON().selected_services, [{ code: '95909', units: 1 }])
    body = { coverage: { status: 'active_today', message: 'Synthetic coverage response.', coverage_start: null, coverage_end: null },
      payer: 'Example Health', checked_at: '2026-10-29T12:00:00Z', checked_on: '2026-10-29', check_ids: ['ec_synthetic'],
      patient_given_name: 'Pat', patient_family_name: 'Example', patient_birth_date: '1980-01-01',
      patient_address: { line1: '100 Example Ave', line2: null, city: 'Albany', state: 'NY', postal_code: '12207', country: null },
      network_status: 'in_network', network_evidence: { reason: 'Synthetic clinic contract.' }, plan_name: 'Example PPO',
      benefits: { copay_cents: 2500, coinsurance_percent: '0', deductible_annual_cents: 100000,
        deductible_remaining_cents: 50000, oop_annual_cents: 300000, oop_remaining_cents: 150000,
        deductible_applicability: 'not_applicable', authorization_signal: null, referral_signal: null, resolved_service_type: '73' },
      lines: [{ code: '95909', units: 1, role: 'diagnostic_test', service_type: '73', allowed_cents: 10000,
        patient_cents: 2500, benefits: { copay_cents: 2500, coinsurance_percent: '0', deductible_annual_cents: 100000,
          deductible_remaining_cents: 50000, oop_annual_cents: 300000, oop_remaining_cents: 150000,
          deductible_applicability: 'not_applicable', authorization_signal: null, referral_signal: null, resolved_service_type: '73' },
        issues: [], conditions: [] }],
      patient_min_cents: 2500, patient_max_cents: 2500, estimate_status: 'priced',
      collection: { amount_cents: 2500, basis: 'anticipated_services', reason: 'Synthetic approved clinic advance.',
        components: { copay_cents: 2500, deductible_cents: 0, coinsurance_cents: 0,
          allowed_cap_adjustment_cents: 0, oop_adjustment_cents: 0 } }, issues: [] }
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
  await page.getByLabel('Reason for visit and any care already discussed').fill('Follow-up for hand numbness')
  assert.equal(checks, 0)
  await page.getByRole('option', { name: 'Example Health (EXAMPLE)' }).click()
  await page.getByLabel('Insurance member ID').focus()
  assert.equal(await page.getByLabel('Insurance member ID').evaluate((input) => document.activeElement === input), true)
  await page.getByRole('button', { name: 'Find possible care' }).click()
  await page.getByText('Possible nerve testing').waitFor()
  assert.equal(await page.getByText('Add a clinician-confirmed CPT/HCPCS code').count(), 0)
  assert.equal(checks, 0)
  await page.getByLabel('Reason for visit and any care already discussed').fill('Follow-up for hand numbness; clinician plans 5 nerve conduction studies')
  await page.getByRole('button', { name: 'Find possible care' }).click()
  await page.getByRole('checkbox', { name: /Nerve conduction, five or six studies/ }).check()
  await page.getByLabel('I reviewed these anticipated services and units with the clinician.').check()
  await page.getByRole('button', { name: 'Check benefits and estimate cost' }).click()
  assert.equal(checks, 1)
  assert.equal(plans, 2)
  await page.getByText('In network for the matched plan and clinic context').waitFor()
  await page.getByText('100 Example Ave').waitFor()
  await page.getByText('Estimated clinic collection after booking').waitFor()
  await page.getByText('DOB 1980-01-01').waitFor()
  console.log('PASS: plain-language care planning and synthetic patient, coverage, network and collection result')
} finally {
  await browser.close()
}
