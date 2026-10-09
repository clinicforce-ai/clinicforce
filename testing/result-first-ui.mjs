// Run against Vite with PLAYWRIGHT_MODULE pointing to an installed Playwright module.
import assert from 'node:assert/strict'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce', locale: 'en-US' })
const errors = [], requests = [], reviews = []
const previsitRequests = []
let lookupLinked = false, confirmations = 0, lookupGate = Promise.resolve(), estimateGate = Promise.resolve(), reviewGate = Promise.resolve()
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
  collection_guidance: { amount_cents: 0, status: 'estimated', basis: 'out_of_pocket_limit', reason: 'The payer reports $0 remaining in-network out-of-pocket responsibility.', patient_message: 'Final responsibility follows the claim.', network: 'Choice PPO', network_source: 'Clinic contract; exact product match not confirmed', requirements: { referral: 'No requirement reported', authorization: 'Unknown in payer response' }, assumptions: ['The provider participates in this plan.'], patient_actions: [], services: [{ id: codes[0].id, code: codes[0].code, benefit: 'Service category 98', patient_cents: 0, oop_remaining_cents: 0 }] },
  emg_option: { requested: false, included: false, message: 'Select testing to calculate its cost.' },
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
  else if (path === '/api/staff/patients/sample/appointments') body = { appointments: [], ecw_status: 'complete' }
  else if (path.endsWith('/appointments')) body = { appointments: [visit] }
  else if (path.endsWith('/appointments/previsit')) { previsitRequests.push(route.request().postDataJSON()); await estimateGate; body = result }
  else if (path.endsWith('/cases/sample-check/previsit')) body = result
  else if (path.endsWith('/payer-response')) body = { source: 'Stedi', response: { id: 'saved-payer-response', benefits: [] } }
  else if (path.endsWith('/visit-review')) {
    const review = route.request().postDataJSON(); reviews.push(review); await reviewGate
    if (review.emg_required != null) result = { ...result, emg_option: { ...result.emg_option, requested: review.emg_required, additional_min_cents: 6000, additional_max_cents: 8000, total_min_cents: 10000, total_max_cents: 12000, message: 'Testing scope determines final cost.' } }
    if (review.selected_ids) result = { ...result, selected_ids: review.selected_ids, codes: codes.filter(row => review.selected_ids.includes(row.id)), service_plan: { ...result.service_plan, selection_source: 'clinician' } }
    if (review.payment_mode) result = { ...result, payment_mode: review.payment_mode }
    result = { ...result, revision: result.revision + 1 }; body = result
  } else if (path === '/api/staff/patients/sample/appointments') body = { appointments: [], ecw_status: 'complete' }
  else if (path.endsWith('/financials')) body = { lines: [], message: 'No receipts', checked_at: '2026-09-28T16:00:00Z' }
  else if (path.endsWith('/history')) body = { visits: [] }
  else if (path.endsWith('/cash-schedules')) body = { schedules: [] }
  else throw new Error('Unexpected API ' + path)
  await route.fulfill({ json: body })
})
async function openPatient(pending = false) {
  await page.goto(process.env.UI_URL || 'http://127.0.0.1:5182/?staff')
  await page.getByRole('button', { name: /Alex Morgan/ }).click()
  await page.getByRole('button', { name: 'Open insurance review', exact: true }).click()
  if (pending) await page.locator('.coverage-chip').filter({ hasText: 'Coverage check in progress' }).waitFor()
  else await page.locator('#visit-estimate .arrival-total > strong').waitFor()
}
async function detail(title) {
  const summary = page.locator('.visit-detail-section > summary').filter({ hasText: title })
  if (!(await summary.locator('..').evaluate(el => el.open))) await summary.click()
  return summary.locator('..')
}
try {
  let releaseEstimate
  estimateGate = new Promise(resolve => { releaseEstimate = resolve })
  const opening = openPatient()
  await page.getByText('Putting the estimate together', { exact: true }).waitFor()
  assert(!(await page.locator('.visit-content').innerText()).includes('$0.00'))
  releaseEstimate(); await opening
  assert.equal(await page.getByText('Reconcile policy details', { exact: true }).count(), 0)
  assert.equal(await page.locator('.policy-reconciliation').count(), 0)
  assert.equal(await page.locator('.arrival-total > strong').innerText(), '$0.00')
  assert.equal(await page.locator('.arrival-total > span').first().innerText(), 'Collect at arrival')
  assert(await page.locator('.visit-context-facts').getByText('Dr. Taylor Lee', { exact: true }).isVisible())
  assert(requests.some(path => path.endsWith('/cards')), 'Cards load independently in background')
  assert.equal(previsitRequests.length, 1)
  assert(await page.locator('.visit-identity-notice').isVisible())
  assert.equal(await page.locator('.visit-snapshots > div').count(), 4)
  assert((await page.locator('.visit-appointment-strip select').innerText()).includes('9:00 AM CDT'))
  assert.deepEqual(await page.locator('.visit-detail-section > summary').allTextContents(), [
    'Possible additional services / modify inputs', 'Detailed benefits, insurance & card',
    'Patient estimate', 'Audit trail & history'])
  assert.equal(await page.locator('.visit-detail-section[open]').count(), 0)
  assert(await page.locator('#visit-estimate').evaluate(el => el.compareDocumentPosition(document.querySelector('.visit-detail-sections')) & Node.DOCUMENT_POSITION_FOLLOWING))
  let section = await detail('Patient estimate')
  assert((await section.innerText()).includes('Estimated patient responsibility · $0.00'))
  await section.locator('summary').click()
  section = await detail('Detailed benefits')
  assert(await section.getByText('Sam Other', { exact: true }).isVisible())
  assert(await section.getByText('Alex Former', { exact: true }).isVisible())
  await section.getByText(/Card preview unavailable/).waitFor()
  assert(await section.getByRole('heading', { name: /Service category 98/ }).isVisible())
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
  assert.equal(await page.getByText('Raw payer response', { exact: true }).count(), 0)
  section = await detail('Audit trail')
  await section.getByText('No completed visits returned in the last year.').waitFor()
  await section.locator('summary').click()
  // Ready amounts, including verified zero, come from collection evidence.
  result = { ...result, collection_guidance: { ...result.collection_guidance, amount_cents: 4000, reason: 'Specialist/office-visit copay returned for this plan.', assumptions: ['Before payments or credits; show a receipt if already paid.'] }, codes: [codes[0]], selected_ids: ['99203'], planning_estimate: null,
    cash_quote: { complete: true, min_cents: 25000, max_cents: 25000, message: 'Clinic self-pay price for the selected office visit.' },
    benefits: { ...benefits, network_status: 'in_network', oop_remaining_cents: 50000 },
    arrival_collection: { amount_cents: 4000, status: 'copay_only', payments_received_cents: null, reason: 'Specialist/office-visit copay returned for this plan.' },
    price_range: { complete: true, mode: 'services', patient_min_cents: 4000, patient_max_cents: 4000, plan_min_cents: 10000, plan_max_cents: 10000, cash_complete: true, cash_min_cents: 25000, cash_max_cents: 25000,
      lines: [{ ...codes[0], benefits: { ...benefits, requirements }, issues: [], calculation: { patient_cents: 4000, plan_cents: 10000, copay_cents: 4000 } }], scenarios: [] } }
  await openPatient()
  assert.equal(await page.locator('.arrival-total > strong').innerText(), '$40.00')
  assert(await page.locator('.arrival-total').getByText(/Specialist\/office-visit copay/).isVisible())
  const beforeEmg = previsitRequests.length
  let releaseReview
  reviewGate = new Promise(resolve => { releaseReview = resolve })
  await page.getByRole('checkbox', { name: 'EMG / nerve testing required' }).click()
  await page.waitForFunction(() => document.querySelector('.arrival-total > strong')?.textContent === 'Calculating…')
  assert(!(await page.locator('.arrival-total').innerText()).includes('$40.00'))
  releaseReview()
  await page.waitForFunction(() => document.querySelector('.arrival-total > strong')?.textContent === '$100.00')
  assert((await page.locator('.emg-option').innerText()).includes('$60.00'))
  assert.equal(previsitRequests.length, beforeEmg)
  assert.equal(reviews.at(-1).emg_required, true)
  await page.getByRole('checkbox', { name: 'EMG / nerve testing required' }).click()
  await page.waitForFunction(() => document.querySelector('.arrival-total > strong')?.textContent === '$40.00')
  await page.locator('.arrival-total details > summary').click()
  assert(await page.getByText(/Before payments or credits/).first().isVisible())
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
  result = { ...result, collection_guidance: { ...result.collection_guidance, amount_cents: 0 }, arrival_collection: { ...result.arrival_collection, amount_cents: 0 } }
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
    await page.locator('#visit-estimate .arrival-total > strong').waitFor()
    assert.equal(previsitRequests.length, before + 1)
    assert.deepEqual(previsitRequests.at(-1), { appointment_id: visit.id, refresh_benefits: false })
    assert.equal(confirmations, 1)
  }
  await page.getByRole('button', { name: 'Check again', exact: true }).click()
  await page.locator('#visit-estimate .arrival-total > strong').waitFor()
  assert.equal(previsitRequests.at(-1).refresh_benefits, true)
  assert(!reviews.some(review => review.identity_confirmation || review.product_mapping || review.payments_received_cents != null))
  // Unresolved evidence defers collection without claiming zero responsibility.
  result = { ...result, collection_guidance: { ...result.collection_guidance, amount_cents: null, status: 'unavailable' } }
  await openPatient()
  assert.equal(await page.locator('.arrival-total > strong').innerText(), 'Review billing details')
  result.collection_guidance = { ...result.collection_guidance, amount_cents: 0, status: 'deferred_billing', basis: 'deferred_billing', patient_message: 'No advance requested; final responsibility remains unknown.' }
  await openPatient()
  assert.equal(await page.locator('.arrival-total > strong').innerText(), 'Review billing details')
  await page.getByRole('button', { name: 'Cash', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.arrival-total > strong')?.textContent === '$250.00')
  assert((await page.locator('.arrival-total').innerText()).includes('Clinic self-pay price'))
  assert(!(await page.locator('.arrival-total').innerText()).includes('No advance payment'))
  await page.getByRole('button', { name: 'Insurance', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('.arrival-total > strong')?.textContent === 'Review billing details')
  const spinal = { id: 'planning:62270', code: '62270', units: 1, description: 'Diagnostic spinal tap · without imaging' }
  result = { ...result, codes: [codes[0], spinal], available_codes: [...codes, spinal], selected_ids: ['99203', spinal.id],
    price_range: { ...result.price_range, cash_complete: false, cash_min_cents: null, cash_max_cents: null } }
  await openPatient()
  assert.equal(await page.locator('.arrival-total > strong').innerText(), 'Review billing details')
  assert((await page.locator('.arrival-total').innerText()).includes('Collect no insurance advance today'))
  section = await detail('Possible additional')
  assert((await section.locator('.selected-service-chips').innerText()).includes('62270'))
  await section.getByRole('button', { name: 'Other care', exact: true }).click()
  assert.equal(await section.getByRole('button', { name: /Diagnostic spinal tap/ }).getAttribute('aria-pressed'), 'true')
  await section.locator('summary').click()
  result = { ...result, benefit_source: 'eCW saved eligibility' }
  await openPatient()
  assert((await page.locator('.arrival-sources').innerText()).includes('eCW saved eligibility'))
  assert(!(await page.locator('.arrival-sources').innerText()).includes('Stedi'))
  result.collection_guidance.reason = 'Calculation/source error: invalid payer financial evidence was rejected. Clinic staff must verify the source before collecting.'
  await openPatient()
  assert((await page.locator('.arrival-total').innerText()).includes('Calculation/source error'))
  assert.equal(await page.locator('.arrival-total > strong').innerText(), 'Review billing details')
  // The same arrival card explains distant appointments without pending/error language.
  result = { ...result, visit_type: 'established', reason: '3 month f/u',
    eligibility_timing: { status: 'too_early', appointment_date: '2026-11-24', check_from: '2026-11-17', message: 'Appointment found on November 24, 2026. Too early to check; reopen November 17.' },
    collection_guidance: { ...result.collection_guidance, amount_cents: null, status: 'too_early', reason: 'Appointment found on November 24, 2026. Too early to check; reopen November 17.',
      requirements: { referral: 'Check closer to visit', authorization: 'Check closer to visit' }, network: 'Check closer to visit', network_source: '' },
    codes: [{ id: '99213', code: '99213', units: 1 }], available_codes: [{ id: '99213', code: '99213', units: 1 }], selected_ids: ['99213'],
    service_plan: { ...result.service_plan, recommended_ids: ['99213'] }, emg_option: undefined }
  await openPatient()
  assert.equal(await page.locator('.arrival-total > strong').innerText(), 'Too early to check')
  assert((await page.locator('.arrival-total').innerText()).includes('November 24'))
  assert(!(await page.locator('#visit-estimate').innerText()).includes('Check pending'))
  assert((await page.locator('.visit-context-facts').innerText()).includes('Established patient'))
  section = await detail('Possible additional')
  assert.equal(await section.getByRole('button', { name: /Follow-up · Low complexity/ }).getAttribute('aria-pressed'), 'true')
  await section.locator('summary').click()
  result = { ...result, eligibility_timing: undefined }
  await openPatient()
  // An EHR collection instruction remains explicit even with an optional testing scenario.
  const billing = { kind: 'no_collection', amount_cents: 0, source: 'Encounter/visit.extension[0]', updated_at: '2026-10-07T10:00:00Z', text: 'Do not collect money from the patient.' }
  result = { ...result, collection_guidance: { ...result.collection_guidance, status: 'no_advance', basis: 'ecw_billing_note', amount_cents: 0, billing_note: billing },
    emg_option: { requested: true, included: false, total_min_cents: 12000 } }
  await openPatient()
  assert.equal(await page.locator('.arrival-total > strong').innerText(), '$0.00')
  await page.getByText('eCW billing note ·', { exact: false }).click()
  assert(await page.getByText(billing.text, { exact: true }).isVisible())
  // Completed empty/failed searches must not retain the pending label or invent a price.
  const beforeRecovery = structuredClone(result)
  for (const [state, outcome, coverageStatus, label] of [
    ['complete', 'no_match', 'NOT_FOUND', 'Coverage not found electronically'],
    ['complete', 'review_needed', 'REVIEW_REQUIRED', 'Coverage unverified · review required'],
    ['complete', 'not_available', 'REVIEW_REQUIRED', 'Coverage unverified · review required'],
    ['rejected', 'rejected', 'REVIEW_REQUIRED', 'Coverage check could not complete'],
    ['indeterminate', null, 'REVIEW_REQUIRED', 'Coverage check outcome unknown'],
    ['pending', null, 'REVIEW_REQUIRED', 'Coverage check in progress'],
  ]) {
    result = { ...beforeRecovery, benefits: null, ecw_coverage_active: false, coverage_status: coverageStatus,
      eligibility_error: 'coverage_identity_missing', recovery: { state, outcome, message: 'Electronic retrieval does not establish that the patient is uninsured.' },
      collection_guidance: { ...beforeRecovery.collection_guidance, billing_note: undefined, status: 'deferred_billing', amount_cents: null, reason: 'Coverage remains unverified.' }, emg_option: undefined }
    await openPatient(state === 'pending')
    assert.equal(await page.locator('.visit-patient-bar .coverage-chip').innerText(), label)
    if (state === 'pending') {
      assert(!(await page.locator('.arrival-total > strong').innerText()).includes('$'))
    } else {
      assert.equal(await page.locator('.visit-snapshots > div').first().locator('strong').innerText(), label)
      assert.equal(await page.locator('.arrival-total > strong').innerText(), 'Review billing details')
      assert.equal(await page.getByRole('button', { name: 'Check again', exact: true }).isDisabled(), false)
    }
  }
  // Terminal automatic recovery changes payment mode without a staff action.
  const reviewsBeforeCash = reviews.length
  result = { ...beforeRecovery, benefits: null, ecw_coverage_active: false, coverage_status: 'REVIEW_REQUIRED',
    payment_mode: 'cash', eligibility_error: 'coverage_identity_missing', emg_option: undefined,
    cash_fallback: { reason: 'Automatic insurance recovery finished. Cash / self-pay pricing selected; insurance remains unverified.' },
    recovery: { state: 'complete', outcome: 'review_needed', exhausted: true, message: 'Available automatic checks finished.',
      candidates: [{ id: 'ambiguous', payer: 'Example Plan', plans: [], member_id: 'SYNTHETIC-FAMILY',
        subscriber: { firstName: 'Parent', lastName: 'Example', dateOfBirth: null },
        dependent: { firstName: 'Alex', lastName: 'Morgan', dateOfBirth: '2001-02-03' },
        verification_status: 'REVIEW_REQUIRED', source: 'stedi_discovery',
        reason_codes: ['SUBSCRIBER_DOB_MISSING', 'ADDITIONAL_IDENTITY_EVIDENCE_REQUIRED'] }],
      payer_routes: [{ id: 'suggested', name: 'Unverified route', aliases: [], names: [] }] },
    insurance_diagnosis: { coverageSource: 'stedi_discovery', rawCandidateCount: 1, retainedCandidateCount: 1,
      verifiedCandidateCount: 0, identityStatus: 'REVIEW_REQUIRED', eligibilityStatus: 'NOT_VERIFIED',
      pricingStatus: 'NOT_CALCULATED', reasonCodes: [], states: [], pricingReasons: ['Additional identity evidence is required.'] },
    price_range: { ...beforeRecovery.price_range, cash_complete: true, cash_min_cents: 16000, cash_max_cents: 16000 },
    collection_guidance: { ...beforeRecovery.collection_guidance, billing_note: undefined, amount_cents: 16000, status: 'cash_estimate', basis: 'practice_cash_price', reason: 'Insurance remains unverified.' } }
  await openPatient()
  assert.equal(await page.locator('.arrival-total > strong').innerText(), '$160.00')
  assert.equal(await page.locator('.coverage-chip').innerText(), 'Coverage unverified · cash pricing')
  assert.equal(reviews.length, reviewsBeforeCash)
  section = await detail('Detailed benefits')
  const candidateSection = section.getByRole('region', { name: 'Returned insurance candidates' })
  assert((await candidateSection.innerText()).includes('SYNTHETIC-FAMILY'))
  assert((await candidateSection.innerText()).includes('Coverage not verified'))
  assert.equal(await candidateSection.locator('div').filter({ has: page.locator('dt', { hasText: 'Policyholder DOB' }) }).locator('dd').innerText(), 'Unknown')
  assert((await candidateSection.innerText()).includes('Alex Morgan · DOB 2001-02-03'))
  assert((await section.getByRole('region', { name: 'Insurance check diagnosis' }).innerText()).includes('1 returned · 1 retained · 0 matched candidates'))
  assert.equal(await section.getByRole('button', { name: 'Check this policy', exact: true }).count(), 1)
  assert.equal(await section.getByRole('heading', { name: 'Select the payer to check', exact: true }).count(), 0)
  // Multiple retained policies and directory routes always have optional dropdowns,
  // including after automatic cash fallback. Viewing a suggestion is not a paid check.
  result.recovery.candidates.push({ ...result.recovery.candidates[0], id: 'second', member_id: 'SECOND-POLICY', payer: 'Another Payer', coverage_evidence: { status: 'EXPIRED', reported_status: 'INACTIVE', start: '2000-01-01', end: '2000-12-31' } })
  result.recovery.payer_routes.push({ id: 'route-2', name: 'Another route', aliases: [], names: [] })
  await openPatient()
  section = await detail('Detailed benefits')
  const suggestions = section.getByRole('combobox', { name: 'Insurance policy suggestions' })
  assert.equal(await suggestions.locator('option').count(), 3)
  const beforeSuggestion = requests.length
  await suggestions.selectOption('second')
  assert((await section.getByRole('region', { name: 'Returned insurance candidates' }).innerText()).includes('2000-12-31'))
  await section.getByRole('combobox', { name: 'Payer suggestions' }).selectOption('route-2')
  assert.equal(requests.length, beforeSuggestion)
  assert.equal(reviews.length, reviewsBeforeCash)
  assert.equal(await page.locator('.arrival-total > strong').innerText(), '$160.00')
  result.price_range = { ...result.price_range, cash_complete: false }
  result.collection_guidance = { ...result.collection_guidance, amount_cents: null, status: 'cash_price_missing', basis: 'practice_cash_price' }
  await openPatient()
  assert(!(await page.locator('.arrival-total > strong').innerText()).includes('$'))
  result = beforeRecovery
  // A failed refresh retains the saved estimate, labelled for rechecking.
  result = { ...result, emg_option: undefined, collection_guidance: { ...result.collection_guidance, status: 'estimated', amount_cents: 4735,
    billing_note: { ...billing, kind: 'office_copay', amount_cents: 4735, text: 'Active, $47.35 copay, ded met' } } }
  result.emg_option = { requested: true, included: false, total_min_cents: null, message: 'Testing needs an applicable rate.' }
  await openPatient()
  assert.equal(await page.locator('.arrival-total > strong').innerText(), '$47.35')
  assert((await page.locator('.arrival-total').innerText()).includes('Testing is not yet priced'))
  await page.route('**/appointments/previsit', route => route.fulfill({ status: 503, json: { detail: 'Payer unavailable' } }))
  await page.getByRole('button', { name: 'Check again', exact: true }).click()
  await page.getByRole('alert').filter({ hasText: 'The saved visit details and estimate are retained below.' }).waitFor()
  assert.equal(await page.locator('.arrival-total > strong').innerText(), '$47.35')
  assert.equal(await page.locator('.arrival-total > span').first().innerText(), 'Previous estimate · needs recheck')
  assert((await page.locator('.arrival-total').innerText()).includes('reconfirm this visit before collecting'))
  assert.deepEqual(errors, [])
  console.log('PASS: immediate estimator entry; collection evidence; warning-only surname difference; provider; detail order; optional cards; service changes; cached benefits; hidden raw response; error fallback; responsive layouts')
} finally { await browser.close() }
