// Run against Vite; all patient, payer, and claim data below is synthetic.
import assert from 'node:assert/strict'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: 'en-US' })
const errors = [], requests = []
page.on('pageerror', error => errors.push(error.message))
const patient = { id: 'sample', given_name: 'Alex', family_name: 'Morgan', birth_date: '2001-02-03', ecw_account_number: '001234' }
const intake = { id: 'current-intake', source: 'ecw_lookup', status: 'ready', patient, fields: { ...patient, ecw_patient_id: 'sample-ecw' }, documents: [], created_at: '2026-09-28T12:00:00Z', updated_at: '2026-09-28T12:00:00Z' }
const benefits = { active: true, network_status: 'in_network', copay_cents: 4000, deductible_remaining_cents: 10000, oop_remaining_cents: 20000, review_reasons: [] }
const result = {
  check_id: 'saved-check', appointment_id: 'completed-visit', service_date: '2025-08-15', payer: 'Example Health', plan: 'Choice PPO', checked_at: '2025-08-14T16:30:00Z', issues: [], suggestions: null,
  patient_details: { ecw_account_number: '001234' }, revision: 2, visit_type: 'established', reason: 'Follow-up headache review', codes: [{ id: '99213', code: '99213', units: 1, source: 'eCW' }],
  service_plan: { kind: 'office', groups: [], recommended_ids: ['99213'], selection_source: 'clinician', recommendation_basis: 'Follow-up consultation', history_count: 2, assumptions: ['Final coding follows care provided.'] },
  insurance_details: { member_id: 'SYNTHETIC123', payer_subscriber: { firstName: 'Alex', lastName: 'Morgan' }, payer_patient: { firstName: 'Alex', lastName: 'Morgan', dateOfBirth: '2001-02-03' }, payer_relationship: 'SELF' },
  benefits, price_range: { complete: false, mode: 'services', lines: [], scenarios: [] },
  collection_guidance: { amount_cents: 4000, status: 'estimated', reason: 'Office copay from the saved payer response.', patient_message: 'Final responsibility follows the claim.', network: 'Choice PPO', requirements: { referral: 'Not required', authorization: 'Not required' }, assumptions: ['Provider participation confirmed for this date.'], patient_actions: [], services: [{ id: '99213', code: '99213', benefit: 'Office visit', patient_cents: 4000, ...benefits }] },
  appointment_context: { rendering_provider: { name: 'Dr. Taylor Lee' }, timezone: 'America/Chicago', imported_fields: [{ label: 'Visit notes', text: 'Return visit: symptoms improved since the previous consultation.', source: 'eCW' }, { label: 'status', text: 'Completed', source: 'eCW' }] },
  audit_trail: [{ checked_at: '2025-08-14T16:30:00Z', by: 'Example clinician', changes: ['Services'] }],
  recovery: { state: 'pending', message: 'Saved pending status must not start polling in history.' },
}
const check = { id: 'saved-check', intake_id: 'past-intake', source_mode: 'commercial_previsit', service_date: '2025-08-15', visit_type: 'established', created_at: '2025-08-14T15:00:00Z', label: 'Visit estimate', pending: true }
const appointments = [{ id: 'completed-visit', appointment_id: 'completed-visit', start: '2025-08-15T09:00:00-05:00', date: '2025-08-15', status: 'finished', reason: 'Morning follow-up', visit_type: 'established', checks: [check] }, { id: 'same-day', start: '2025-08-15T14:00:00-05:00', date: '2025-08-15', status: 'finished', reason: 'Afternoon visit without an estimate', checks: [] }, ...Array.from({ length: 8 }, (_, i) => ({ id: `old-${i}`, date: '2010-01-01', start: '2010-01-01', status: 'finished', reason: 'Older visit', checks: [] }))]
const snapshots = [
  { result, patient, saved_at: '2025-08-14T16:30:00Z', stage: 'visit-review' },
  { result: { ...result, revision: 1, collection_guidance: { ...result.collection_guidance, amount_cents: 2500 } }, patient, saved_at: '2025-08-14T15:00:00Z', stage: 'previsit-complete' },
]
await page.route(url => url.pathname.startsWith('/api/'), async route => {
  const req = route.request(), path = new URL(req.url()).pathname
  requests.push({ path, method: req.method() })
  let body
  if (path === '/api/staff/session') body = { staff: { id: 'staff', practice_id: 'clinic', practice_name: 'Example Clinic', display_name: 'Clinician', role: 'admin' } }
  else if (path === '/api/staff/patients/dashboard') body = { patients: [{ ...patient, clinic_patient_id: patient.id, identity_status: 'confirmed', account_status: 'clinic', card_status: 'missing', intakes: [], intake_ids: [] }], counts: { all: 1 } }
  else if (path === '/api/staff/patients/sample/appointments') body = { appointments, ecw_status: 'complete', ecw_account_number: '001234' }
  else if (path === '/api/staff/intakes/search') body = { intakes: [intake], has_more: false }
  else if (path === '/api/staff/intakes/current-intake' || path === '/api/staff/intakes/past-intake') body = { ...intake, id: path.split('/').at(-1) }
  else if (path.endsWith('/checks/saved-check/financials')) body = { lines: [{ transaction_id: 'claim', claim_reference: 'claim-1', line_index: 0, code: '99213', modifiers: [], service_date: '20250815', payer: 'Example Health', allowed_cents: 12500, insurer_paid_cents: 9000, patient_responsibility_cents: 3500, claim_status: '1', received_at: '2025-09-01' }], checked_at: '2026-10-01T12:00:00Z', source: 'Payer remittance', message: 'One processed claim matched.', partial: false }
  else if (path === '/api/staff/intakes/past-intake/checks/saved-check') body = { snapshots }
  else if (path === '/api/staff/intakes/current-intake/checks') body = { checks: [{ id: 'saved-check', intake_id: 'past-intake', source_mode: 'commercial_previsit', service_date: '2025-08-15', visit_type: 'established', created_at: '2025-08-14T15:00:00Z', label: 'Visit estimate', pending: true }] }
  else throw new Error(`Unexpected request: ${req.method()} ${path}`)
  await route.fulfill({ json: body })
})
try {
  await page.goto(process.env.UI_URL || 'http://127.0.0.1:5197/?staff')
  await page.getByRole('button', { name: 'Insurance inbox', exact: true }).click()
  await page.getByRole('button', { name: /Alex Morgan/ }).click()
  assert.equal(await page.getByText('Saved insurance checks', { exact: true }).count(), 0)
  await page.getByRole('button', { name: 'View patient appointments' }).click()
  await page.getByRole('heading', { name: 'Appointments and saved estimates' }).waitFor()
  assert.equal(await page.locator('.patient-appointment').count(), 7)
  await page.getByRole('button', { name: 'Next appointments' }).click()
  assert.equal(await page.locator('.patient-appointment').count(), 3)
  await page.getByRole('button', { name: 'Previous appointments' }).click()
  const sameDay = page.locator('.patient-appointment').filter({ hasText: 'Afternoon visit without an estimate' })
  assert.match(await sameDay.innerText(), /No saved Clinicforce estimate is linked/)
  assert.equal(await sameDay.locator('.staff-saved-checks').count(), 0)
  await page.getByText('View saved estimate results', { exact: true }).click()
  await page.getByRole('button', { name: '2025-08-15 · Established / follow-up patient' }).waitFor()
  const archive = page.locator('.visit-estimator')
  await archive.getByRole('heading', { name: 'Saved insurance estimate' }).waitFor()
  assert.match(await archive.innerText(), /\$40\.00/)
  assert.match(await archive.innerText(), /Return visit: symptoms improved/)
  assert.match(await archive.innerText(), /Established patient/)
  assert.match(await archive.innerText(), /SYNTHETIC123/)
  assert.match(await archive.innerText(), /eCW ACCT: 001234/)
  assert.match(await archive.locator('#visit-detail-services').innerText(), /CPT 99213 × 1/)
  assert.match(await archive.innerText(), /Dr\. Taylor Lee/)
  assert.equal(await archive.locator('button, input, select, textarea').count(), 0)
  assert.equal(await archive.locator('details:not([open])').count(), 0)
  await page.getByLabel('Recorded versions').selectOption('1')
  await page.waitForFunction(() => document.querySelector('.arrival-total')?.textContent.includes('$25.00'))
  await page.getByText('Compare with processed claims', { exact: true }).click()
  await page.getByText('One processed claim matched.', { exact: true }).waitFor()
  assert.match(await page.locator('.financial-history').innerText(), /\$35\.00/)
  for (const width of [1440, 768, 375, 320]) {
    await page.setViewportSize({ width, height: 1000 })
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) {
      console.log(await page.evaluate(() => [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth && e.getBoundingClientRect().width > 0).slice(-20).map(e => ({ tag: e.tagName, cls: e.className, right: e.getBoundingClientRect().right, width: e.getBoundingClientRect().width }))))
      if (process.env.SCREENSHOT_PATH) await page.screenshot({ path: process.env.SCREENSHOT_PATH, fullPage: true })
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `Overflow at ${width}`)
  }
  await page.waitForTimeout(2500)
  assert.deepEqual(errors, [])
  assert.equal(requests.some(row => row.path.startsWith('/api/doctor/insurance')), false, 'Archive must not start a current estimate or poll recovery')
  assert.equal(requests.filter(row => !row.path.endsWith('/search')).every(row => row.method === 'GET'), true)
  if (process.env.SCREENSHOT_PATH) await page.screenshot({ path: process.env.SCREENSHOT_PATH, fullPage: true })
  console.log('Patient appointment history passed: matching visits, same-day separation, pagination, ACCT, full established-patient record, frozen revisions, no editing or payer checks, scoped claims, responsive layouts.')
} finally { await browser.close() }
