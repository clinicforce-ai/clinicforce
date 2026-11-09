// Synthetic browser regression; payer and check requests never leave the browser.
import assert from 'node:assert/strict'

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH })
const page = await browser.newPage()
let checks = 0
let priced = false
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
    const isEmg = route.request().postDataJSON().reason.includes('EMG')
    const code = isEmg ? '99213' : '99203'
    body = { groups: [
      { label: 'Office visit included in estimate', selection_mode: 'single', codes: [{ code, reason: 'Office visit planning baseline; the billed level can differ.', role: 'consultation', service_type: '98', selected_by_default: true }] },
      ...(isEmg ? [{ label: 'Possible testing', selection_mode: 'multiple', codes: [{ code: '95909', reason: 'Nerve study explicitly mentioned in the visit reason.', role: 'diagnostic', service_type: '73', selected_by_default: false }] }] : []),
    ], notes: isEmg ? [] : ['No separate test is established.'], message: 'Starting estimate.' }
  } else if (url.pathname === '/api/staff/prebooking-estimate/check') {
    checks++
    const inquiry = route.request().postDataJSON()
    assert.equal(inquiry.member_id.startsWith('SYNTHETIC'), true)
    assert.equal(inquiry.network_context_id, 'context-1')
    const officeCode = inquiry.visit_type === 'established' ? '99213' : '99203'
    assert.equal(inquiry.selected_services.some((service) => service.code === officeCode && service.units === 1), true)
    const addedEmg = inquiry.selected_services.some((service) => service.code === '95909')
    assert.equal(inquiry.selected_services.length, addedEmg ? 2 : 1)
    if (addedEmg) assert.equal(inquiry.selected_services.find((service) => service.code === '95909').units, 1)
    body = priced ? { coverage: { status: 'active_today', message: 'Synthetic coverage response.', coverage_start: null, coverage_end: null },
      payer: 'Example Health', checked_at: '2026-10-29T12:00:00Z', checked_on: '2026-10-29', check_ids: ['ec_synthetic'],
      patient_address: { line1: '100 Example Ave', line2: null, city: 'Albany', state: 'NY', postal_code: '12207', country: null },
      network_status: 'in_network', network_evidence: { source: 'clinic_library_contract', reason: 'Synthetic NMCT contract.' }, plan_name: 'Example PPO',
      benefits: { copay_cents: 2500, coinsurance_percent: '0', deductible_annual_cents: 100000, deductible_remaining_cents: 40000,
        oop_annual_cents: 300000, oop_remaining_cents: 120000 },
      lines: [{ code: officeCode, units: 1, role: 'visit', service_type: '98', allowed_cents: 10000, patient_cents: 2500,
        rate_source: 'Synthetic signed rate', benefits: { copay_cents: 2500 }, issues: [], conditions: [] },
        ...(addedEmg ? [{ code: '95909', units: 1, role: 'diagnostic', service_type: '73', allowed_cents: 20000, patient_cents: 5000,
          rate_source: 'Synthetic EMG rate', benefits: { coinsurance_percent: '25' }, issues: [], conditions: [] }] : [])],
      allowed_total_cents: addedEmg ? 30000 : 10000, plan_total_cents: addedEmg ? 22500 : 7500,
      patient_min_cents: addedEmg ? 7500 : 2500, patient_max_cents: addedEmg ? 7500 : 2500, estimate_status: 'priced', issues: [] }
      : { coverage: { status: 'needs_review', message: 'The insurer did not match this person and member ID.', coverage_start: null, coverage_end: null },
      payer: 'Example Health', checked_at: '2026-10-29T12:00:00Z', checked_on: '2026-10-29', check_ids: ['ec_synthetic'],
      patient_address: { line1: '100 Example Ave', line2: null, city: 'Albany', state: 'NY', postal_code: '12207', country: null },
      network_status: 'unknown', network_evidence: { reason: 'Contract scope unresolved.' }, plan_name: null, benefits: null,
      lines: [],
      allowed_total_cents: null, plan_total_cents: null,
      patient_min_cents: null, patient_max_cents: null, estimate_status: 'needs_review', issues: ['The insurer did not match this person and member ID.'] }
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
  await page.getByLabel('Insurance company').fill('Example')
  await page.getByLabel('Reason for visit').fill('headache')
  assert.equal(checks, 0)
  await page.getByRole('option', { name: 'Example Health (EXAMPLE)' }).click()
  await page.getByRole('button', { name: 'Check insurance and estimate visit' }).click()
  assert.equal(plans, 0)
  assert.equal(checks, 0)
  await page.getByLabel('Member ID from insurance card').fill('SYNTHETIC')
  await page.getByLabel('Member ID from insurance card').focus()
  assert.equal(await page.getByLabel('Member ID from insurance card').evaluate((input) => document.activeElement === input), true)
  await page.getByRole('button', { name: 'Check insurance and estimate visit' }).click()
  await page.getByText('Coverage not confirmed').waitFor()
  assert.equal(plans, 1)
  assert.equal(checks, 1)
  assert.match(await page.locator('.prebooking-status-row').innerText(), /did not match this person/)
  assert.match(await page.locator('.prebooking-unavailable').innerText(), /No reliable visit price/)
  assert.equal(await page.getByText('Needs review').count(), 0)
  assert.equal(await page.getByLabel('Add a clinician-confirmed CPT/HCPCS code').count(), 0)
  assert.match(await page.locator('.prebooking-baseline').innerText(), /99203/)

  priced = true
  await page.getByLabel('Member ID from insurance card').fill('SYNTHETIC2')
  await page.getByRole('button', { name: 'Check insurance and estimate visit' }).click()
  await page.getByText('Synthetic NMCT contract.').waitFor()
  assert.equal(checks, 2)
  assert.equal(plans, 1)
  const totals = await page.locator('.prebooking-cost-grid').innerText()
  assert.match(totals, /\$100\.00/)
  assert.match(totals, /\$25\.00/)
  assert.match(await page.locator('.prebooking-share-labels').innerText(), /\$75\.00/)
  assert.match(await page.locator('.prebooking-service-row').innerText(), /Synthetic signed rate/)
  assert.equal(await page.locator('.prebooking-benefit-section').getByText('Deductible remaining').count(), 1)
  await page.getByLabel('Office visit type').selectOption('established')
  await page.getByLabel('Reason for visit').fill('hand numbness; EMG planned')
  await page.getByRole('button', { name: 'Check insurance and estimate visit' }).click()
  await page.getByText('Possible additions from the visit reason').waitFor()
  assert.equal(plans, 2)
  assert.equal(checks, 3)
  assert.match(await page.locator('.prebooking-baseline').innerText(), /99213/)
  await page.getByLabel(/95909.*Nerve study/).check()
  await page.getByRole('button', { name: 'Check insurance and estimate visit' }).click()
  await page.getByText('Synthetic EMG rate').waitFor()
  assert.equal(checks, 4)
  assert.match(await page.locator('.prebooking-cost-grid').innerText(), /\$300\.00/)
  assert.match(await page.locator('.prebooking-cost-grid').innerText(), /\$75\.00/)
  assert.match(await page.locator('.prebooking-share-labels').innerText(), /\$225\.00/)
  if (process.env.PREBOOKING_SCREENSHOT) await page.screenshot({ path: process.env.PREBOOKING_SCREENSHOT, fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true)
  console.log('PASS: prebooking review and priced totals reconcile in responsive staff UI')
} finally {
  await browser.close()
}
