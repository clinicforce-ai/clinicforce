// Run against Vite with PLAYWRIGHT_MODULE pointing to an installed Playwright module.
import assert from 'node:assert/strict'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce', locale: 'en-US' })
const errors = [], requests = [], reviews = []
const previsitRequests = []
let lookupLinked = false, confirmations = 0
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
  appointment_context: { timezone: 'America/Chicago', imported_fields: [{ label: 'General notes', text: 'Patient comment\nPlease explain the treatment plan.', source: 'Encounter/sample.extension' }, { label: 'status', text: 'PEN (Pending)', source: 'Encounter/private-record.extension[0](https://ehr.example/status).valueString' }] },
  mapping_candidate: { network: 'Choice PPO', products: ['Choice Medical'], reference: 'Network record' },
}
await page.route(url => url.pathname.startsWith('/api/'), async route => {
  const path = new URL(route.request().url()).pathname
  requests.push(path)
  let body = {}
  if (path === '/api/staff/session') body = { staff: { id: 'staff', practice_id: 'clinic', practice_name: 'Example Clinic', display_name: 'Clinician', role: 'admin' } }
  else if (path === '/api/staff/patients/dashboard') body = { patients: [{ ...patient, clinic_patient_id: patient.id, account_status: 'clinic', card_status: 'missing', identity_status: 'confirmed', intakes: [intake], intake_ids: [intake.id], latest_intake_id: intake.id }], counts: { all: 1, other: 1 } }
  else if (path === '/api/staff/intakes/ecw-lookup') body = { ...intake, patient: lookupLinked ? patient : null, ecw_match: { patient, appointments: [visit] } }
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
async function noForms() {
  assert.equal(await page.locator('.visit-estimator input, .visit-estimator textarea, .visit-estimator form, .visit-estimator details').count(), 0, 'Estimate flow has no text entry, forms or accordions')
}
try {
  await openPatient()
  assert.equal(await page.locator('.arrival-total > strong').innerText(), '$0.00')
  assert.equal(await page.locator('.arrival-total > span').first().innerText(), 'Planning estimate')
  assert(await page.locator('#visit-estimate').evaluate(el => el.compareDocumentPosition(document.getElementById('visit-plan')) & Node.DOCUMENT_POSITION_FOLLOWING))
  assert(requests.some(path => path.endsWith('/cards')), 'Cards fetch before details open')
  assert.equal(requests.filter(path => path.endsWith('/appointments/previsit')).length, 1)
  assert(await page.locator('.visit-identity-notice').isVisible())
  assert.equal(await page.locator('.visit-snapshots > div').count(), 4)
  assert((await page.locator('.visit-snapshots').innerText()).includes('Prior authorization'))
  assert((await page.locator('.visit-appointment-strip select').innerText()).includes('9:00 AM CDT'))
  await noForms()
  if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: process.env.SCREENSHOT_DIR + '/visual-estimate-desktop.png', fullPage: true })
  await page.getByRole('button', { name: /Coverage & card/ }).click()
  await page.getByRole('dialog').waitFor()
  assert(await page.getByRole('dialog').getByText('Sam Other', { exact: true }).isVisible())
  assert(await page.getByRole('dialog').getByText('Alex Former', { exact: true }).isVisible())
  assert(!(await page.getByRole('dialog').innerText()).includes('Service category 98'))
  await page.getByText(/Card preview unavailable/).waitFor()
  await page.keyboard.press('Escape')
  assert.equal(await page.getByRole('dialog').count(), 0)
  assert(await page.getByRole('button', { name: /Coverage & card/ }).evaluate(el => document.activeElement === el), 'Dialog restores focus')
  await page.getByRole('button', { name: /Appointment notes/ }).click()
  assert.equal(await page.locator('.visit-imported article p').innerText(), 'Patient comment\nPlease explain the treatment plan.')
  assert(await page.getByText('Appointment status: PEN (Pending)', { exact: true }).isVisible())
  assert(!(await page.getByRole('dialog').innerText()).includes('Encounter/'), 'Technical provenance stays out of the clinician panel')
  assert.equal(await page.getByRole('heading', { name: 'General Notes not returned' }).count(), 0)
  await page.getByRole('button', { name: 'Close details' }).click()
  await page.getByRole('button', { name: 'Adjust services' }).click()
  await page.getByRole('button', { name: /Moderate complexity/ }).click()
  await page.getByRole('button', { name: 'Recalculate estimate' }).click()
  await page.waitForFunction(() => document.querySelector('.visit-care-cards')?.textContent.includes('99204'))
  assert.equal(reviews.at(-1).selected_ids[0], '99204')
  assert.equal(requests.filter(path => path.endsWith('/appointments/previsit')).length, 1, 'Service change reuses payer response')
  await page.getByRole('button', { name: 'Adjust services' }).click()
  await page.getByRole('button', { name: 'Nerve study', exact: true }).click()
  await page.getByRole('button', { name: /Nerve conduction · 7–8 studies/ }).click()
  await page.getByRole('button', { name: 'Muscle EMG', exact: true }).click()
  await page.getByRole('button', { name: /Complete muscle examination/ }).click()
  await page.getByRole('button', { name: 'Increase 95886 quantity', exact: true }).click()
  await page.getByRole('button', { name: 'Recalculate estimate' }).click()
  await page.getByRole('button', { name: 'Adjust services' }).waitFor()
  assert.equal(reviews.at(-1).confirmed_units['95886'], 2, 'Quantity buttons persist units')
  await page.getByRole('button', { name: /Sources & history/ }).click()
  await page.getByText('No completed visits returned in the last year.').waitFor()
  assert(await page.getByText('Service category 98', { exact: true }).isVisible())
  assert.equal(requests.filter(path => path.endsWith('/payer-response')).length, 0)
  const checksBeforeSource = previsitRequests.length
  await page.getByRole('button', { name: 'View saved payer response', exact: true }).click()
  await page.locator('.visit-raw-response').waitFor()
  assert((await page.locator('.visit-raw-response').innerText()).includes('saved-payer-response'))
  assert.equal(previsitRequests.length, checksBeforeSource)
  await page.getByRole('button', { name: 'Close details' }).click()
  for (const width of [375, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 })
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `No overflow at ${width}`)
    if (width === 375 && process.env.SCREENSHOT_DIR) await page.screenshot({ path: process.env.SCREENSHOT_DIR + '/visual-estimate-mobile.png', fullPage: true })
    await page.getByRole('button', { name: 'Adjust services' }).click()
    assert(await page.getByRole('dialog').evaluate(el => el.scrollWidth <= el.clientWidth), `Dialog fits ${width}`)
    await noForms()
    await page.keyboard.press('Escape')
  }
  // A completed nonzero calculation: chart must describe actual patient/plan amounts.
  result = { ...result, planning_estimate: null, identity: { ...result.identity, status: 'matched', differences: [] },
    benefits: { ...benefits, network_status: 'in_network', oop_remaining_cents: 50000 },
    arrival_collection: { amount_cents: 31352, estimated_cents: 31352, status: 'estimate', payments_received_cents: null },
    price_range: { complete: true, mode: 'services', patient_min_cents: 31352, patient_max_cents: 31352, plan_min_cents: 10000, plan_max_cents: 10000, cash_complete: true, cash_min_cents: 25000, cash_max_cents: 25000,
      lines: [{ ...codes[0], benefits, issues: [], calculation: { patient_cents: 31352, plan_cents: 10000, copay_cents: 0, deductible_cents: 30060, coinsurance_cents: 1292 } }] } }
  await openPatient()
  assert.equal(await page.locator('.arrival-total > strong').innerText(), '$313.52')
  assert(await page.getByRole('img', { name: 'Patient $313.52, insurer $100.00' }).isVisible())
  assert(await page.getByText('For this visit, before payments or credits.', { exact: true }).isVisible())
  if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: process.env.SCREENSHOT_DIR + '/visual-estimate-nonzero.png', fullPage: true })
  await page.getByRole('button', { name: 'Cash', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.arrival-total > strong')?.textContent === '$250.00')
  assert.equal(await page.locator('.arrival-total > span').first().innerText(), 'Cash price')
  await page.getByRole('button', { name: 'Insurance', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.arrival-total > strong')?.textContent === '$313.52')
  // Missing evidence stays unavailable, never an invented zero.
  result = { ...result, issues: ['No exact product contract is linked.'], arrival_collection: { amount_cents: null, status: 'verify' }, price_range: { ...result.price_range, complete: false }, benefits: { ...benefits, network_status: 'unknown', oop_remaining_cents: null } }
  await openPatient()
  assert.equal(await page.locator('.arrival-total > strong').innerText(), 'Not available yet')
  assert(await page.getByText('No exact product contract is linked.', { exact: true }).isVisible())
  await noForms()
  result = { ...result, appointment_context: { ...result.appointment_context, imported_fields: [{ label: 'status', text: 'PEN (Pending)', source: 'Encounter/sample.extension[0]' }] } }
  const cardMessage = 'The current eCW connection did not return a card preview. A card saved in eCW folders may still be available there. Card previews are optional for estimates.'
  await page.route('**/api/staff/intakes/*/cards', route => route.fulfill({ json: { cards: [], message: cardMessage } }))
  await openPatient()
  await page.getByRole('button', { name: /Coverage & card/ }).click()
  await page.getByText(cardMessage, { exact: true }).waitFor()
  assert(!(await page.getByRole('dialog').innerText()).includes('Scan or upload'))
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: /Appointment notes/ }).click()
  await page.getByRole('heading', { name: 'General Notes not returned' }).waitFor()
  assert(!(await page.getByRole('dialog').innerText()).includes('Encounter/'))
  if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: process.env.SCREENSHOT_DIR + '/general-notes-unavailable.png' })
  await page.keyboard.press('Escape')
  const beforeReload = previsitRequests.length
  await page.getByRole('button', { name: 'Refresh visits', exact: true }).click()
  await page.locator('.arrival-total > strong').waitFor()
  assert.equal(previsitRequests.length, beforeReload + 1, 'Refreshing the same visit restarts the estimate')
  result = { ...result, price_range: { ...result.price_range, lines: [{ ...result.price_range.lines[0], benefits: { ...benefits, requirements: { ...requirements, authorization: { required: 'not_required', status: 'denied', cleared: false } } } }] } }
  await openPatient()
  assert((await page.locator('.visit-snapshots').innerText()).includes('Denied'), 'Conflicting denial stays visible even with a not-required signal')
  // New and existing name/DOB lookups both go directly to a fresh estimate.
  for (const existing of [false, true]) {
    const before = previsitRequests.length
    await page.goto(process.env.UI_URL || 'http://127.0.0.1:5182/?staff')
    await page.getByRole('button', { name: 'Pull patient from eCW', exact: true }).click()
    await page.getByLabel('First name', { exact: true }).fill('Alex')
    await page.getByLabel('Last name', { exact: true }).fill('Morgan')
    await page.getByLabel('Date of birth', { exact: true }).fill('2001-02-03')
    await page.getByRole('button', { name: 'Pull from eCW', exact: true }).click()
    await page.locator('.arrival-total > strong').waitFor()
    assert.equal(previsitRequests.length, before + 1, `${existing ? 'Existing' : 'New'} patient starts one check`)
    assert.deepEqual(previsitRequests.at(-1), { appointment_id: visit.id, refresh_benefits: true })
    assert.equal(confirmations, 1, 'Existing lookup reuses the linked record')
    await noForms()
  }
  await page.getByRole('button', { name: 'Check again', exact: true }).click()
  await page.locator('.arrival-total > strong').waitFor()
  assert.equal(previsitRequests.at(-1).refresh_benefits, true)
  assert(!reviews.some(review => review.identity_confirmation || review.product_mapping || review.payments_received_cents != null), 'No manufactured verification or payments')
  assert.deepEqual(errors, [])
  console.log('PASS: result first; no forms/accordions; card failure isolated; keyboard focus; click-only services; cash/insurance; accurate graphics; missing evidence; responsive layouts')
} finally { await browser.close() }
