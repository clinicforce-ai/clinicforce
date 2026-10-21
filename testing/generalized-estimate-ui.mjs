// Run against local Vite with PLAYWRIGHT_MODULE and CHROMIUM_EXECUTABLE_PATH.
import assert from 'node:assert/strict'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE_PATH || undefined })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' })
page.setDefaultTimeout(6000)
const errors = [], reviews = [], previsitRequests = []
page.on('pageerror', error => errors.push(error.stack || error.message))
page.on('console', message => {
  if (message.type() === 'error' && message.text().includes('Each child in a list'))
    errors.push(message.text())
})
const patient = { id: 'sample', given_name: 'Alex', family_name: 'Morgan', birth_date: '2001-02-03' }
const visits = [
  { id: 'visit-a', start: '2026-10-21T09:00:00-04:00', status: 'planned', reason: 'Office consultation' },
  { id: 'visit-b', start: '2026-10-22T10:00:00-04:00', status: 'planned', reason: 'Botox injection' },
  { id: 'visit-c', start: '2026-10-23T10:00:00-04:00', status: 'planned', reason: 'EMG/NCS testing' },
]
const intake = { id: 'sample-intake', source: 'ecw_lookup', status: 'ready', patient,
  fields: { ...patient, ecw_patient_id: 'synthetic-ecw' }, documents: [],
  created_at: '2026-10-20T12:00:00Z', updated_at: '2026-10-20T12:00:00Z' }
const office = { id: 'office', code: '99214', source: 'CPT planning catalog', units: 1,
  service_family: 'office_consultation', service_role: 'consultation' }
const drug = { id: 'drug', code: 'J0585', source: 'CPT planning catalog', units: null,
  service_family: 'botulinum_drug', service_role: 'drug', benefit_service_types: ['1'] }
const procedure = { id: 'procedure', code: '64612', source: 'CPT planning catalog', units: 1,
  service_family: 'chemodenervation', service_role: 'procedure', benefit_service_types: ['1', '2'] }
const baseBenefits = { active: true, network_status: 'unknown', requires_review: true,
  review_reasons: [], reported_options: [
    { id: 'office-row', kind: 'copay', service_type_code: '98', network: 'IN_NETWORK',
      time_period: 'VISIT', amount_cents: 4000 },
    { id: 'drug-row', kind: 'coinsurance', service_type_code: '33', network: 'IN_NETWORK',
      time_period: 'VISIT', percent: '20' },
    { id: 'medical-row', kind: 'coinsurance', service_type_code: '1', network: 'IN_NETWORK',
      time_period: 'VISIT', percent: '20' },
  ] }
const makeResult = (visit, available, recommendation) => ({
  check_id: `check-${visit.id}`, appointment_id: visit.id,
  service_date: visit.start.slice(0, 10), payer: 'Synthetic Blue Plan', plan: 'Blue PPO',
  checked_at: '2026-10-21T08:00:00Z', benefit_source: 'Saved synthetic eligibility',
  revision: 1, context_token: visit.id === 'visit-a' ? 'a'.repeat(64) : 'b'.repeat(64),
  visit_type: 'established', reason: visit.reason, payment_mode: 'insurance',
  issues: [], codes: [], available_codes: available, selected_ids: [],
  service_selection_status: 'preview',
  service_plan: { kind: visit.id === 'visit-b' ? 'botox' : 'office',
    recommended_ids: recommendation, selection_source: 'suggested', groups: [] },
  identity: { status: 'matched', match_version: 2, binding: 'synthetic-bound-member', differences: [] },
  benefits: baseBenefits,
  price_range: { complete: false, mode: 'services', lines: [], service_issues: [] },
  collection_guidance: { amount_cents: null, status: 'action_required',
    basis: 'missing_collection_inputs', reason: 'Select and approve planned care.',
    network: 'Network not established', network_source: 'Provider participation missing',
    patient_message: 'An estimate is not ready.', requirements: {}, assumptions: [],
    patient_actions: [], services: [] },
  arrival_collection: { amount_cents: null, status: 'verify' },
  appointment_context: { timezone: 'America/New_York', rendering_provider: { name: 'Dr. Taylor Lee' } },
})
const results = {
  'visit-a': makeResult(visits[0], [office], ['office']),
  'visit-b': makeResult(visits[1], [drug, procedure, office], []),
}
const ncsChoices = Array.from({ length: 7 }, (_, index) => ({
  id: `ncs-option:${95907 + index}`, code: String(95907 + index), units: 1,
  source: 'conditional NCS alternative', service_group: 'nerve',
}))
const ncsBenefits = { ...baseBenefits, network_status: 'in_network',
  network_source: 'clinic_library_contract', requires_review: false,
  service_date_matches: true, has_secondary: false, reported_options: [] }
results['visit-c'] = {
  ...makeResult(visits[2], ncsChoices, []), codes: ncsChoices,
  selected_service_count: 1,
  provisional_scenario: { kind: 'ncs_study_count', count: 1,
    candidate_ids: ncsChoices.map(row => row.id), basis: 'eCW booked EMG/NCS visit' },
  service_plan: { kind: 'emg_ncs', recommended_ids: [], selection_source: 'suggested', groups: [] },
  benefits: ncsBenefits,
  price_range: { mode: 'alternatives', complete: true, conditional: false,
    patient_min_cents: 6000, patient_max_cents: 8000, service_issues: [],
    lines: ncsChoices.map((row, index) => ({ ...row, benefits: ncsBenefits,
      allowed_cents: 10000 + index * 2000, issues: [], calculation: { patient_cents: 6000 + index * 333 } })) },
  planning_estimate: { amount_cents: null, min_cents: 6000, max_cents: 8000,
    collectable: false, basis: 'conditional_services', explanation: 'Alternative study-count range', assumptions: [] },
  requirement_evidence: { procedures: ncsChoices.map(row => ({ id: row.id, code: row.code,
    referral: { status: 'Verification required', reason: 'Exact product unknown', sources: [] },
    authorization: { status: 'Verification required', reason: 'Exact product unknown', sources: [] } })) },
}
results['visit-a'].network_evidence = {
  decision: 'in_network_contract_supported', outcome: 'IN_NETWORK_CONTRACT_SUPPORTED',
  network_name: 'Blue Choice PPO', document_id: 'synthetic-contract',
  reason: "NMCT has a Blue Choice PPO contract, but this doctor's participation isn't established in the available records.",
}
page.on('requestfailed', request => errors.push(`${request.method()} ${request.url()} ${request.failure()?.errorText}`))
await page.route(url => url.pathname.startsWith('/api/'), async route => {
  const path = new URL(route.request().url()).pathname
  let body
  if (path === '/api/staff/session') body = { staff: { id: 'staff', practice_id: 'clinic', practice_name: 'Example Clinic', role: 'admin' } }
  else if (path === '/api/staff/patients/dashboard') body = { patients: [{ ...patient, clinic_patient_id: patient.id,
    account_status: 'clinic', card_status: 'missing', identity_status: 'confirmed',
    intakes: [intake], intake_ids: [intake.id], latest_intake_id: intake.id }], counts: { all: 1, other: 1 } }
  else if (path === '/api/staff/intakes/sample-intake') body = intake
  else if (path === '/api/staff/intakes/search') body = { intakes: [intake], has_more: false }
  else if (path === '/api/staff/intakes/ecw-lookup') body = { ...intake, patient, ecw_match: { patient, appointments: visits } }
  else if (path === '/api/staff/patients/sample/appointments') body = { appointments: [], ecw_status: 'complete' }
  else if (path.endsWith('/appointments')) body = { appointments: visits }
  else if (path.endsWith('/appointments/previsit')) {
    const request = route.request().postDataJSON()
    previsitRequests.push(request)
    body = results[request.appointment_id]
  } else if (path.endsWith('/visit-review')) {
    const review = route.request().postDataJSON()
    reviews.push(review)
    const id = path.includes('check-visit-a') ? 'visit-a' : path.includes('check-visit-c') ? 'visit-c' : 'visit-b'
    const previous = results[id]
    const chosen = review.selected_ids || previous.selected_ids
    body = results[id] = { ...previous, revision: previous.revision + 1,
      selected_ids: chosen, codes: chosen.map(key => {
        const row = previous.available_codes.find(candidate => candidate.id === key)
        return { ...row, units: review.confirmed_units?.[key] || row.units }
      }), service_selection_status: 'approved',
      service_plan: { ...previous.service_plan, selection_source: 'clinician' },
      ...(review.botox ? { botox_review: review.botox } : {}),
      ...(id === 'visit-c' ? { price_range: { ...previous.price_range, mode: 'services',
        patient_min_cents: 7000, patient_max_cents: 7000,
        lines: chosen.map(key => ({ ...previous.price_range.lines.find(line => line.id === key),
          calculation: { patient_cents: 7000 } })) } } : {}),
    }
  } else if (path.endsWith('/checks')) body = { checks: [] }
  else if (path.endsWith('/cards')) body = { cards: [] }
  else if (path.endsWith('/history')) body = { visits: [] }
  else if (path.endsWith('/financials')) body = { lines: [], message: 'No receipts' }
  else if (path.endsWith('/cash-schedules')) body = { schedules: [] }
  else if (path === '/api/staff/library') body = { documents: [] }
  else throw new Error(`Unexpected API path: ${path}`)
  await route.fulfill({ json: body })
})
const openPatient = async () => {
  await page.goto(process.env.UI_URL || 'http://127.0.0.1:5182/?staff')
  await page.getByRole('button', { name: /Alex Morgan/ }).click()
  await page.getByRole('button', { name: 'Open insurance review', exact: true }).click().catch(async error => {
    console.error((await page.locator('body').innerText()).slice(0, 1600), errors)
    throw error
  })
  await page.locator('#visit-estimate').waitFor().catch(async error => {
    console.error((await page.locator('body').innerText()).slice(0, 1800), errors)
    throw error
  })
}
const openServices = async () => {
  const section = page.locator('#visit-detail-services')
  if (!(await section.evaluate(element => element.open))) await section.locator('summary').click()
  return section
}
try {
  await openPatient()
  assert(await page.getByText('Contract indicates in network').isVisible())
  assert(await page.getByText(results['visit-a'].network_evidence.reason).isVisible())
  let section = await openServices()
  assert.equal(await section.locator('.selected-service-chips').innerText(), 'No services selected',
    'Suggested office code stays separate from clinician selection')
  await section.getByRole('button', { name: /Moderate complexity/ }).click()
  await section.getByRole('button', { name: 'Recalculate estimate' }).click()
  await page.waitForFunction(() => document.querySelector('#visit-detail-services .selected-service-chips')?.textContent.includes('99214'))
  assert.deepEqual(reviews.at(-1).selected_ids, ['office'])
  section = await openServices()
  const evidence = section.getByRole('heading', { name: 'Review evidence for each selected service' })
  assert(await evidence.isVisible(), 'Generic line evidence review appears for an office code')
  await section.getByLabel('Payer reference or Decision ID').fill('SYNTHETIC-PORTAL-1')
  await section.getByLabel('What the payer confirmed').fill('Member-specific office copay confirmed for this visit.')
  await section.getByLabel('Applicable cost share').selectOption('copay')
  await section.getByLabel(/copay · 98 · category · office-row/).check()
  const reviewCount = reviews.length
  const savedReview = page.waitForResponse(response => response.url().endsWith('/visit-review'))
  await section.getByRole('button', { name: 'Save line verification' }).click()
  await savedReview
  assert.equal(reviews.length, reviewCount + 1)
  assert.equal(reviews.at(-1).verification.line_id, 'office')
  assert.equal(reviews.at(-1).verification.service_type_code, '98')
  assert.deepEqual(reviews.at(-1).verification.selected_ids, ['office-row'])
  await openPatient()
  section = await openServices()
  assert((await section.locator('.selected-service-chips').innerText()).includes('99214'),
    'Reopening keeps the approved office line')
  await page.locator('.visit-appointment-strip select').selectOption('visit-b')
  section = await openServices()
  assert.equal(await section.locator('.selected-service-chips').innerText(), 'No services selected',
    'Switching appointments clears prior line selection')
  assert((await section.innerText()).includes('Botox clinical review'))
  await section.getByRole('button', { name: /Botox medication/ }).click()
  await section.getByRole('button', { name: /Facial muscle injections/ }).click()
  for (let index = 0; index < 8; index++)
    await section.getByRole('button', { name: 'Increase J0585 by 10 units' }).click()
  await section.getByLabel('Chart or order reference').fill('Synthetic chart order')
  await section.getByLabel('Modifier coding review reference').fill('Synthetic modifier review')
  await section.getByLabel('Documented medical indication').fill('Synthetic documented indication')
  await section.getByLabel('Injection site or anatomical region').fill('Synthetic facial region')
  await section.getByLabel('Encounter evidence').selectOption('planned')
  await section.getByLabel('Drug source').selectOption('clinic')
  await section.getByLabel('Planned or administered brand').selectOption('Botox')
  await section.getByLabel('Planned HCPCS units for this visit').fill('80')
  await section.getByRole('button', { name: 'Recalculate estimate' }).click()
  await page.waitForFunction(() => document.querySelector('#visit-detail-services .selected-service-chips')?.textContent.includes('J0585'))
  assert.deepEqual(reviews.at(-1).selected_ids.sort(), ['drug', 'procedure'])
  assert.equal(reviews.at(-1).botox.planned_units, 80)
  section = await openServices()
  await section.getByRole('combobox', { name: /Selected service/ }).selectOption('drug')
  assert.equal(await section.getByRole('option', { name: 'Service category 33' }).count(), 0,
    'A chiropractic row is not offered for a Botox drug line')
  await section.getByLabel('Payer-confirmed service category').selectOption('1')
  await section.getByLabel('Applicable cost share').selectOption('coinsurance')
  await section.getByLabel(/coinsurance · 1 · category · medical-row/).check()
  await section.getByLabel('Payer reference or Decision ID').fill('SYNTHETIC-DRUG-PORTAL')
  await section.getByLabel('What the payer confirmed').fill('Synthetic member drug benefit for planned service.')
  const drugSaved = page.waitForResponse(response => response.url().endsWith('/visit-review'))
  await section.getByRole('button', { name: 'Save line verification' }).click()
  await drugSaved
  assert.equal(reviews.at(-1).verification.line_id, 'drug')
  assert.equal(reviews.at(-1).verification.service_type_code, '1',
    'Only a clinically compatible payer category is offered for this drug')
  await page.locator('.visit-appointment-strip select').selectOption('visit-c')
  assert((await page.locator('#visit-estimate strong[role="status"]').innerText()).includes('US$60.00 – US$80.00'),
    'A complete source-backed NCS range appears as the provisional headline')
  section = await openServices()
  assert((await section.locator('.selected-service-chips').innerText()).includes('One NCS scenario'))
  assert.equal(await section.locator('.procedure-requirements').count(), 0,
    'Alternative CPT cards do not repeat unresolved requirements seven times')
  const ncsSaved = page.waitForResponse(response => response.url().endsWith('/visit-review'))
  await section.getByRole('button', { name: /Nerve conduction.*95908/ }).click()
  await ncsSaved
  await page.waitForFunction(() => document.querySelector('#visit-detail-services .selected-service-chips')?.textContent.includes('95908'))
  assert.deepEqual(reviews.at(-1).selected_ids, ['ncs-option:95908'])
  assert.deepEqual(results['visit-c'].codes.map(row => row.code), ['95908'])
  assert.deepEqual(results['visit-c'].price_range.lines.map(row => row.code), ['95908'])
  assert.equal(errors.length, 0, errors.join('\n'))
  assert(previsitRequests.length >= 2)
  console.log('Generalized estimate browser workflow: passed')
} finally {
  await browser.close()
}
