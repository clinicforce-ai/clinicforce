// Run against Vite with PLAYWRIGHT_MODULE pointing to an installed Playwright module.
import assert from 'node:assert/strict'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce', locale: 'en-US' })
const errors = [], requests = [], reviews = []
const previsitRequests = []
let lookupLinked = false, confirmations = 0, lookupGate = Promise.resolve()
page.on('pageerror', e => errors.push(e.message))
const patient = { id: 'sample', given_name: 'Alex', family_name: 'Morgan', birth_date: '2001-02-03' }
const visit = { id: 'visit-a', start: '2026-09-29T09:00:00-05:00', status: 'planned', reason: 'New patient neurology consultation' }
const intake = { id: 'sample-intake', source: 'ecw_lookup', status: 'ready', patient, fields: { ...patient, ecw_patient_id: 'sample-ecw' }, documents: [], created_at: '2026-09-28T12:00:00Z', updated_at: '2026-09-28T12:00:00Z' }
const requirements = { referral: { required: 'not_required', status: 'not_applicable', cleared: true }, authorization: { required: 'not_required', status: 'not_applicable', cleared: true } }
const benefits = { active: true, network_status: 'unknown', oop_remaining_cents: 0, requires_review: true, review_reasons: [], reported_options: [{ kind: 'copay', service_type_code: '98', network: 'IN_NETWORK', amount_cents: 4000, time_period: 'DAY' }], requirements }
const codes = ['99203', '99204', '95910', '95886', 'J0585'].map(code => ({ id: code, code, source: 'Planning catalog', units: 1 }))
let result = {
  check_id: 'sample-check', appointment_id: visit.id, service_date: '2026-09-29', payer: 'Example Health', plan: 'Choice PPO', checked_at: '2026-09-28T16:30:00Z', issues: [], suggestions: null,
  revision: 1, context_token: 'a'.repeat(64), visit_type: 'new', codes: [codes[0]], available_codes: codes, selected_ids: ['99203'],
  service_plan: { kind: 'office', groups: [], recommended_ids: ['99203'], selection_source: 'suggested', recommendation_basis: 'Office visit', history_count: 0 },
  insurance_details: { member_id: 'ABC123', payer_subscriber: { firstName: 'Sam', lastName: 'Other' }, payer_patient: { firstName: 'Alex', lastName: 'Former', dateOfBirth: '2001-02-03' }, payer_relationship: 'CHILD' },
  identity: { status: 'warning', message: 'Payer surname differs; first name, date of birth and member ID match.', binding: 'b'.repeat(64), can_confirm: true, differences: [{ field: 'last_name', ecw: 'Morgan', payer: 'Former' }] },
  benefits, price_range: { complete: false, mode: 'services', lines: [{ ...codes[0], benefits, issues: [], calculation: null }], scenarios: [] },
  arrival_collection: { amount_cents: null, reason: 'Verify patient and contract.', status: 'verify' },
  planning_estimate: { basis: 'out_of_pocket_limit', amount_cents: 0, collectable: false, explanation: 'The payer reports $0 remaining in-network out-of-pocket responsibility.', assumptions: ['The patient names refer to the same person.', 'The provider participates in this plan.'] },
  appointment_context: { rendering_provider: { name: 'Dr. Taylor Lee', npi: '1234567890' }, timezone: 'America/Chicago', imported_fields: [{ label: 'General notes', text: 'Patient comment\nPlease explain the treatment plan.', source: 'Encounter/sample.extension' }, { label: 'status', text: 'PEN (Pending)', source: 'Encounter/private-record.extension[0](https://ehr.example/status).valueString' }] },
  mapping_candidate: { network: 'Choice PPO', products: ['Choice Medical'], reference: 'Network record' },
}
await page.route(url => url.pathname.startsWith('/api/'), async route => {
  const path = new URL(route.request().url()).pathname
  requests.push(path)
  let body = {}
  if (path === '/api/staff/session') body = { staff: { id: 'staff', practice_id: 'clinic', practice_name: 'Example Clinic', display_name: 'Clinician', role: 'admin' } }
  else if (path === '/api/staff/patients/dashboard') body = { patients: [{ ...patient, clinic_patient_id: patient.id, account_status: 'clinic', card_status: 'missing', identity_status: 'confirmed', intakes: [intake], intake_ids: [intake.id], latest_intake_id: intake.id }], counts: { all: 1, other: 1 } }
  else if (path === '/api/staff/intakes/ecw-lookup') { await lookupGate; body = { ...intake, patient: lookupLinked ? patient : null, ecw_match: { patient, appointments: [visit] } } }
  else if (path === '/api/staff/patients/search') body = { patients: [] }
  else if (path === '/api/staff/intakes/sample-intake/patient') { lookupLinked = true; confirmations++; body = intake }
  else if (path === '/api/staff/intakes/search') body = { intakes: [intake], has_more: false }
  else if (path === '/api/staff/intakes/sample-intake') body = intake
  else if (path.endsWith('/cards')) return route.fulfill({ status: 502, json: { detail: 'Unavailable card interface' } })
  else if (path.endsWith('/checks')) body = { checks: [] }
  else if (path.endsWith('/appointments')) body = { appointments: [visit] }
  else if (path.endsWith('/appointments/previsit')) { previsitRequests.push(route.request().postDataJSON()); body = result }
  else if (path.endsWith('/payer-response')) body = { source: 'Stedi', response: { id: 'saved-payer-response', benefits: [] } }
  else if (path.endsWith('/visit-review')) {
    const review = route.request().postDataJSON(); reviews.push(review)
    if (review.selected_ids) result = { ...result, selected_ids: review.selected_ids, codes: codes.filter(row => review.selected_ids.includes(row.id)), service_plan: { ...result.service_plan, selection_source: 'clinician' } }
    if (review.payment_mode) result = { ...result, payment_mode: review.payment_mode }
    result = { ...result, revision: result.revision + 1 }; body = result
  } else if (path.endsWith('/financials')) body = { lines: [], message: 'No receipts', checked_at: '2026-09-28T16:00:00Z' }
  else if (path.endsWith('/history')) body = { visits: [] }
  else if (path.endsWith('/cash-schedules')) body = { schedules: [] }
  else throw new Error('Unexpected API ' + path)
  await route.fulfill({ json: body })
})
async function openPatient() {
  await page.goto(process.env.UI_URL || 'http://127.0.0.1:5182/?staff')
  await page.getByRole('button', { name: /Alex Morgan/ }).click()
  await page.getByRole('button', { name: 'Open insurance review', exact: true }).click()
  await page.locator('.arrival-total > strong').waitFor()
}
async function detail(title) {
  const summary = page.locator('.visit-detail-section > summary').filter({ hasText: title })
  if (!(await summary.locator('..').evaluate(el => el.open))) await summary.click()
  return summary.locator('..')
}
try {
  await openPatient()
  assert.equal(await page.locator('.arrival-total > strong').innerText(), 'Needs verification')
  assert.equal(await page.locator('.arrival-total > span').first().innerText(), 'Collect at arrival')
  assert(await page.locator('.visit-context-facts').getByText('Dr. Taylor Lee', { exact: true }).isVisible())
  assert(requests.some(path => path.endsWith('/cards')), 'Cards load independently in background')
  assert.equal(previsitRequests.length, 1)
  assert(await page.locator('.visit-identity-notice').isVisible())
  assert.equal(await page.locator('.visit-snapshots > div').count(), 4)
  assert((await page.locator('.visit-appointment-strip select').innerText()).includes('9:00 AM CDT'))
  assert.deepEqual(await page.locator('.visit-detail-section > summary').allTextContents(), [
    'Possible additional services / modify inputs', 'Detailed benefits, insurance & card',
    'Raw payer response', 'Patient estimate', 'Audit trail & history'])
  assert.equal(await page.locator('.visit-detail-section[open]').count(), 0)
  assert(await page.locator('#visit-estimate').evaluate(el => el.compareDocumentPosition(document.querySelector('.visit-detail-sections')) & Node.DOCUMENT_POSITION_FOLLOWING))
  let section = await detail('Patient estimate')
  assert((await section.innerText()).includes('Estimated patient responsibility · $0.00'))
  await section.locator('summary').click()
  section = await detail('Detailed benefits')
  assert(await section.getByText('Sam Other', { exact: true }).isVisible())
  assert(await section.getByText('Alex Former', { exact: true }).isVisible())
  await section.getByText(/Card preview unavailable/).waitFor()
  assert(await section.getByText('Service category 98', { exact: true }).isVisible())
  assert.equal(await section.locator('.visit-imported article p').innerText(), 'Patient comment\nPlease explain the treatment plan.')
  assert(!(await section.innerText()).includes('Encounter/'), 'Technical reference stays out of appointment notes')
  await section.locator('summary').click()
  section = await detail('Possible additional')
  await section.getByRole('button', { name: /Moderate complexity/ }).click()
  await section.getByRole('button', { name: 'Recalculate estimate' }).click()
  await page.waitForFunction(() => document.querySelector('.visit-notice')?.textContent.includes('Estimate updated'))
  assert.equal(reviews.at(-1).selected_ids[0], '99204')
  assert.equal(previsitRequests.length, 1, 'Service changes reuse protected payer evidence')
  await section.getByRole('button', { name: 'Nerve study', exact: true }).click()
  await section.getByRole('button', { name: /Nerve conduction · 7–8 studies/ }).click()
  await section.getByRole('button', { name: 'Muscle EMG', exact: true }).click()
  await section.getByRole('button', { name: /Complete muscle examination/ }).click()
  await section.getByRole('button', { name: 'Increase 95886 quantity', exact: true }).click()
  await section.getByRole('button', { name: 'Recalculate estimate' }).click()
  await page.waitForFunction(() => document.querySelector('.visit-content')?.getAttribute('aria-busy') === 'false')
  assert.equal(reviews.at(-1).confirmed_units['95886'], 2)
  await section.locator('summary').click()
  assert.equal(requests.filter(path => path.endsWith('/payer-response')).length, 0)
  section = await detail('Raw payer response')
  await section.getByRole('button', { name: 'View saved payer response' }).click()
  await page.locator('.visit-raw-response').waitFor()
  assert((await page.locator('.visit-raw-response').innerText()).includes('saved-payer-response'))
  assert.equal(previsitRequests.length, 1)
  await section.locator('summary').click()
  section = await detail('Audit trail')
  await section.getByText('No completed visits returned in the last year.').waitFor()
  await section.locator('summary').click()
  // Ready amounts, including verified zero, come from collection evidence.
  result = { ...result, codes: [codes[0]], selected_ids: ['99203'], planning_estimate: null,
    benefits: { ...benefits, network_status: 'in_network', oop_remaining_cents: 50000 },
    arrival_collection: { amount_cents: 4000, status: 'copay_only', payments_received_cents: null, reason: 'Specialist/office-visit copay returned for this plan.' },
    price_range: { complete: true, mode: 'services', patient_min_cents: 4000, patient_max_cents: 4000, plan_min_cents: 10000, plan_max_cents: 10000, cash_complete: true, cash_min_cents: 25000, cash_max_cents: 25000,
      lines: [{ ...codes[0], benefits: { ...benefits, requirements }, issues: [], calculation: { patient_cents: 4000, plan_cents: 10000, copay_cents: 4000 } }], scenarios: [] } }
  await openPatient()
  assert.equal(await page.locator('.arrival-total > strong').innerText(), '$40.00')
  assert(await page.locator('.arrival-total').getByText(/Specialist\/office-visit copay/).isVisible())
  assert(await page.getByText(/Before payments or credits/).isVisible())
  for (const width of [320, 375, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 })
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `No overflow at ${width}`)
    if (process.env.SCREENSHOT_DIR && [375, 1440].includes(width)) await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/arrival-estimator-${width}.png`, fullPage: true })
    section = await detail('Possible additional')
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `Service inputs fit ${width}`)
    await section.locator('summary').click()
  }
  await page.getByRole('button', { name: 'Cash', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.arrival-total > strong')?.textContent === '$250.00')
  await page.getByRole('button', { name: 'Insurance', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.arrival-total > strong')?.textContent === '$40.00')
  result = { ...result, arrival_collection: { ...result.arrival_collection, amount_cents: 0 } }
  await openPatient()
  assert.equal(await page.locator('.arrival-total > strong').innerText(), '$0.00')
  // Name + DOB enters estimator before lookup completes; new and linked records reuse fresh benefits.
  for (const existing of [false, true]) {
    lookupLinked = existing
    await page.goto(process.env.UI_URL || 'http://127.0.0.1:5182/?staff')
    await page.getByRole('button', { name: 'Pull patient from eCW', exact: true }).click()
    await page.getByLabel('First name', { exact: true }).fill('Alex')
    await page.getByLabel('Last name', { exact: true }).fill('Morgan')
    await page.getByLabel('Date of birth', { exact: true }).fill('2001-02-03')
    const before = previsitRequests.length
    let release
    lookupGate = new Promise(resolve => { release = resolve })
    await page.getByRole('button', { name: 'Open insurance estimator', exact: true }).click()
    await page.getByRole('heading', { name: 'Insurance estimator', exact: true }).waitFor()
    assert(await page.getByText('Finding the patient and scheduled appointment', { exact: true }).isVisible())
    release()
    await page.locator('.arrival-total > strong').waitFor()
    assert.equal(previsitRequests.length, before + 1)
    assert.deepEqual(previsitRequests.at(-1), { appointment_id: visit.id, refresh_benefits: false })
    assert.equal(confirmations, 1)
  }
  await page.getByRole('button', { name: 'Check again', exact: true }).click()
  await page.locator('.arrival-total > strong').waitFor()
  assert.equal(previsitRequests.at(-1).refresh_benefits, true)
  assert(!reviews.some(review => review.identity_confirmation || review.product_mapping || review.payments_received_cents != null))
  assert.deepEqual(errors, [])
  console.log('PASS: immediate estimator entry; collection evidence; warning-only surname difference; provider; detail order; optional cards; service changes; cached benefits; raw response; responsive layouts')
} finally { await browser.close() }
