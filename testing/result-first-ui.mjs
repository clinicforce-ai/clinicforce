// Run against Vite with PLAYWRIGHT_MODULE pointing to an installed Playwright module.
import assert from 'node:assert/strict'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce', locale: 'en-US' })
const errors = [], requests = []
page.on('pageerror', e => errors.push(e.message))
const patient = { id: 'sample', given_name: 'Alex', family_name: 'Morgan', birth_date: '2001-02-03' }
const visit = { id: 'visit-a', start: '2026-09-29T09:00:00-05:00', status: 'planned', reason: 'New patient neurology consultation' }
const intake = { id: 'sample-intake', source: 'ecw_lookup', status: 'ready', patient, fields: { ...patient, ecw_patient_id: 'sample-ecw' }, documents: [], created_at: '2026-09-28T12:00:00Z', updated_at: '2026-09-28T12:00:00Z' }
const requirements = { referral: { required: 'not_required', status: 'not_applicable', cleared: true }, authorization: { required: 'not_required', status: 'not_applicable', cleared: true } }
const benefits = { active: true, network_status: 'unknown', oop_remaining_cents: 0, requires_review: true, review_reasons: ['Confirm patient identity: patient surname differs.'], reported_options: [], requirements }
const codes = ['99203', '99204'].map(code => ({ id: code, code, source: 'Planning catalog', units: 1 }))
let result = {
  check_id: 'sample-check', appointment_id: visit.id, service_date: '2026-09-29', payer: 'Example Health', plan: 'Choice PPO', checked_at: '2026-09-28T16:30:00Z', issues: [], suggestions: null,
  revision: 1, context_token: 'a'.repeat(64), visit_type: 'new', codes: [codes[0]], available_codes: codes, selected_ids: ['99203'],
  service_plan: { kind: 'office', groups: [], recommended_ids: ['99203'], selection_source: 'suggested', recommendation_basis: 'Office visit', history_count: 0 },
  insurance_details: { member_id: 'ABC123', payer_subscriber: { firstName: 'Sam', lastName: 'Other' }, payer_patient: { firstName: 'Alex', lastName: 'Former', dateOfBirth: '2001-02-03' }, payer_relationship: 'CHILD' },
  identity: { status: 'review', binding: 'b'.repeat(64), can_confirm: true, differences: [{ field: 'last_name', ecw: 'Morgan', payer: 'Former' }] },
  benefits, price_range: { complete: false, mode: 'services', lines: [{ ...codes[0], benefits, issues: [], calculation: null }], scenarios: [] },
  arrival_collection: { amount_cents: null, reason: 'Verify patient and contract.', status: 'verify' },
  planning_estimate: { amount_cents: 0, collectable: false, explanation: 'The payer reports $0 remaining in-network out-of-pocket responsibility.', assumptions: ['The patient names refer to the same person.', 'The provider participates in this plan.'] },
  appointment_context: { timezone: 'America/Chicago', imported_fields: [{ label: 'General notes', text: 'Patient comment\nPlease explain the treatment plan.', source: 'Encounter/sample.extension' }] },
  mapping_candidate: { network: 'Choice PPO', products: ['Choice Medical'], reference: 'Network record' },
}
await page.route(url => url.pathname.startsWith('/api/'), async route => {
  const path = new URL(route.request().url()).pathname
  requests.push(path)
  let body = {}
  if (path === '/api/staff/session') body = { staff: { id: 'staff', practice_id: 'clinic', practice_name: 'Example Clinic', display_name: 'Clinician', role: 'admin' } }
  else if (path === '/api/staff/patients/dashboard') body = { patients: [{ ...patient, clinic_patient_id: patient.id, account_status: 'clinic', card_status: 'missing', identity_status: 'confirmed', intakes: [intake], intake_ids: [intake.id], latest_intake_id: intake.id }], counts: { all: 1, other: 1 } }
  else if (path === '/api/staff/intakes/ecw-lookup') body = { ...intake, ecw_match: { patient, appointments: [visit] } }
  else if (path === '/api/staff/intakes/search') body = { intakes: [intake], has_more: false }
  else if (path === '/api/staff/intakes/sample-intake') body = intake
  else if (path.endsWith('/cards')) return route.fulfill({ status: 502, json: { detail: 'Unavailable card interface' } })
  else if (path.endsWith('/checks')) body = { checks: [] }
  else if (path.endsWith('/appointments')) body = { appointments: [visit] }
  else if (path.endsWith('/appointments/previsit')) body = result
  else if (path.endsWith('/visit-review')) {
    const review = route.request().postDataJSON()
    if (review.identity_confirmation) result = { ...result, identity: { ...result.identity, status: 'reconciled', evidence: review.identity_confirmation } }
    if (review.selected_ids) result = { ...result, selected_ids: review.selected_ids, codes: codes.filter(row => review.selected_ids.includes(row.id)) }
    if (review.product_mapping) {
      assert.equal(review.product_mapping.reference, 'Contract page 2')
      result = { ...result, benefits: { ...benefits, network_status: 'in_network' }, planning_estimate: null, arrival_collection: { amount_cents: 0, estimated_cents: 0, status: 'estimate' } }
    }
    result = { ...result, revision: result.revision + 1 }; body = result
  } else if (path.endsWith('/financials')) body = { lines: [], message: 'No receipts', checked_at: '2026-09-28T16:00:00Z' }
  else if (path.endsWith('/history')) body = { visits: [] }
  else if (path.endsWith('/cash-schedules')) body = { schedules: [] }
  else throw new Error('Unexpected API ' + path)
  await route.fulfill({ json: body })
})
try {
  await page.goto(process.env.UI_URL || 'http://127.0.0.1:5182/?staff')
  await page.getByRole('button', { name: /Alex Morgan/ }).click()
  await page.getByRole('button', { name: 'Open insurance review', exact: true }).click()
  await page.getByRole('heading', { name: 'Estimated patient responsibility', exact: true }).waitFor()
  assert.equal(await page.locator('.arrival-total > strong').innerText(), '$0.00')
  assert(await page.locator('#visit-estimate').evaluate(el => el.compareDocumentPosition(document.getElementById('visit-plan')) & Node.DOCUMENT_POSITION_FOLLOWING))
  assert.equal(await page.getByRole('button', { name: 'Continue to visit services' }).count(), 0)
  assert(requests.some(path => path.endsWith('/cards')), 'Cards fetch automatically without opening details')
  assert.equal(requests.filter(path => path.endsWith('/appointments/previsit')).length, 1)
  assert((await page.locator('.visit-appointment-strip select').innerText()).includes('9:00 AM CDT'))
  assert.equal(await page.locator('.visit-imported article p').innerText(), 'Patient comment\nPlease explain the treatment plan.')
  await page.locator('#visit-coverage > summary').click()
  await page.getByLabel('Evidence reference', { exact: true }).fill('Patient card 123')
  await page.getByLabel('How the names were verified').fill('Patient confirmed the previous surname on the current insurance policy.')
  await page.getByLabel('I verified that both patient names identify this patient on this policy.').check()
  await page.getByRole('button', { name: 'Save patient relationship and recalculate' }).click()
  await page.getByText(/Patient and policy relationship confirmed/).waitFor()
  await page.getByRole('radio', { name: /Moderate complexity/ }).check()
  await page.getByRole('button', { name: 'Recalculate estimate' }).click()
  await page.waitForFunction(() => document.querySelector('.procedure-choice.is-selected')?.textContent.includes('99204'))
  await page.locator('#visit-sources > summary').click()
  await page.getByLabel('Valid through', { exact: true }).fill('2026-12-31')
  await page.getByLabel('Contract / portal reference').fill('Contract page 2')
  await page.getByLabel('Verification details').fill('This product uses the verified clinic network and contract rates.')
  await page.getByLabel('I verified this product’s network and contract for the stated dates.').check()
  await page.getByRole('button', { name: 'Save clinic mapping and recalculate' }).click()
  await page.getByRole('heading', { name: 'Collect when the patient arrives' }).waitFor()
  assert.equal(await page.locator('.arrival-total > strong').innerText(), '$0.00')
  assert.equal(requests.filter(path => path.endsWith('/appointments/previsit')).length, 1, 'Reviews reuse the protected payer response')
  await page.locator('#visit-estimate').scrollIntoViewIfNeeded()
  if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: process.env.SCREENSHOT_DIR + '/result-first-desktop.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'No mobile horizontal overflow')
  if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: process.env.SCREENSHOT_DIR + '/result-first-mobile.png', fullPage: true })
  await page.goto(process.env.UI_URL || 'http://127.0.0.1:5182/?staff')
  await page.getByRole('button', { name: 'Pull new patient from ECW', exact: true }).click()
  await page.getByLabel('First name', { exact: true }).fill('Alex')
  await page.getByLabel('Last name', { exact: true }).fill('Morgan')
  await page.getByLabel('Date of birth', { exact: true }).fill('2001-02-03')
  await page.getByRole('button', { name: 'Pull from eCW', exact: true }).click()
  await page.getByRole('heading', { name: 'Collect when the patient arrives' }).waitFor()
  assert.equal(await page.locator('.arrival-total > strong').innerText(), '$0.00')
  assert.deepEqual(errors, [])
  console.log('PASS: automatic result, background card failure, same-page services, identity confirmation, admin mapping, clinic timezone, mobile layout')
} finally { await browser.close() }
