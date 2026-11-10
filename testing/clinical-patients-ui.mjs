// Synthetic browser regression; every API request is intercepted.
import assert from 'node:assert/strict'

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
const errors = []
page.on('pageerror', (error) => errors.push(error.message))

function day(offset) {
  const now = new Date()
  const date = new Date(now.getFullYear(), now.getMonth(), now.getDate() + offset)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

const future = day(2)
const past = day(-3)
const today = day(0)
const savedCheck = {
  id: 'saved-estimate', intake_id: 'synthetic-intake', source_mode: 'commercial_previsit',
  service_date: today, created_at: `${today}T12:00:00Z`, label: 'Visit estimate', pending: false,
}
const olderCheck = { ...savedCheck, id: 'older-estimate', created_at: `${past}T12:00:00Z` }
let savedAmount = 5000
let projectedAmount = 5000
let savedResult
let dashboardReads = 0
let appointmentReads = 0
let useCache = false
const patients = Array.from({ length: 6 }, (_, index) => ({
  id: String(index + 1),
  clinic_patient_id: String(index + 1),
  given_name: 'Patient',
  family_name: String(index + 1).padStart(2, '0'),
  birth_date: '2000-01-01',
  account_status: 'portal',
  card_status: index % 2 ? 'missing' : 'received',
  identity_status: 'confirmed',
  ecw_linked: index !== 3,
  intakes: [],
  intake_ids: [],
  latest_intake_id: index === 5 ? 'synthetic-intake' : null,
}))

await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
  const path = new URL(route.request().url()).pathname
  let body
  if (path === '/api/staff/session') {
    body = {
      staff: {
        id: 'staff',
        practice_id: 'clinic',
        practice_name: 'Example Clinic',
        display_name: 'Clinician',
        role: 'admin',
      },
    }
  } else if (path === '/api/staff/intakes/search') {
    body = { intakes: [], has_more: false }
  } else if (path === '/api/staff/intakes/synthetic-intake') {
    body = { id: 'synthetic-intake', patient: { ...patients[5], ecw_patient_id: 'ecw-6' }, fields: {}, status: 'reviewed' }
  } else if (path === '/api/doctor/insurance/commercial/appointments') {
    body = { appointments: [] }
  } else if (path === '/api/staff/patients/dashboard') {
    dashboardReads += 1
    body = {
      patients: patients.map((patient) => useCache ? {
        ...patient,
        ecw_visit_cached_at: new Date().toISOString(),
        ecw_visit_cache: patient.id === '1'
          ? [{ date: future, start: `${future}T15:00:00-04:00`, status: 'planned' }, { date: past, start: null, status: 'finished' }]
          : patient.id === '2' ? [{ date: future, start: `${future}T09:00:00-04:00`, status: 'booked' }]
            : patient.id === '3' ? Array.from({ length: 6 }, (_, index) => ({ date: day(-3 - index), start: null, status: 'planned' }))
              : patient.id === '6' ? [{ date: today, start: null, status: 'booked' }] : [],
      } : patient),
      counts: { all: 6, accounts_with_cards: 3, accounts_without_cards: 3, card_only: 0, other: 0 },
    }
  } else if (path.endsWith('/appointments')) {
    appointmentReads += 1
    const patientId = path.split('/')[4]
    body = {
      ecw_status: patientId === '5' ? 'incomplete' : 'complete',
      appointments:
        patientId === '1'
          ? [
              { id: 'future-1', from_ecw: true, status: 'planned', date: future, start: `${future}T15:00:00-04:00` },
              { id: 'past-1', from_ecw: true, status: 'finished', date: past },
            ]
          : patientId === '2'
            ? [{ id: 'future-2', from_ecw: true, status: 'booked', date: future, start: `${future}T09:00:00-04:00` }]
            : patientId === '3'
              ? [{ id: 'past-planned', from_ecw: true, status: 'planned', date: past }]
              : patientId === '6'
              ? [{ id: 'today-6', from_ecw: true, status: 'booked', date: today, checks: [olderCheck, savedCheck] }]
              : [],
    }
  } else if (path === '/api/staff/intakes/synthetic-intake/checks/saved-estimate') {
    body = { snapshots: [{
      saved_at: `${today}T12:30:00Z`, stage: 'previsit-complete', patient: patients[5],
      result: {
        check_id: 'saved-estimate', appointment_id: 'today-6', service_date: today,
        revision: 2, visit_type: 'new', reason: 'Synthetic visit review',
        payer: 'Example Health', plan: 'Example PPO', benefit_source: 'synthetic payer response',
        checked_at: `${today}T12:25:00Z`, issues: [], suggestions: null,
        patient_details: { ecw_account_number: '0006', address: { city: 'Example City', state: 'TX' } },
        identity: { match_version: 2, status: 'matched', binding: 'synthetic identity match', can_confirm: true, differences: [] },
        codes: [{ id: '99204', code: '99204', units: 1, source: 'synthetic visit' }],
        service_plan: { kind: 'office', groups: [], recommended_ids: ['99204'], selection_source: 'clinician', recommendation_basis: 'Synthetic visit', history_count: 0, assumptions: [] },
        insurance_details: { member_id: 'SYNTHETIC', payer_subscriber: { firstName: 'Patient', lastName: '06' }, payer_patient: { firstName: 'Patient', lastName: '06', dateOfBirth: '2000-01-01' }, payer_relationship: 'SELF' },
        benefits: { active: true, network_status: 'in_network', network_source: 'clinic_library_contract', service_date_matches: true, copay_cents: 5000, deductible_remaining_cents: 10000, oop_remaining_cents: 20000, has_secondary: false, review_reasons: [] },
        price_range: { complete: false, mode: 'services', lines: [], scenarios: [] },
        collection_guidance: { amount_cents: savedAmount, status: 'estimated', basis: 'office_copay', reason: 'Synthetic office copay.', patient_message: 'Final responsibility follows the claim.', network: 'Example PPO', requirements: { referral: 'Not required', authorization: 'Not required' }, assumptions: [], patient_actions: [], services: [] },
        appointment_context: { rendering_provider: { name: 'Dr. Example' }, timezone: 'America/Chicago' },
        audit_trail: [],
      },
    }] }
    body.snapshots.push({
      ...body.snapshots[0], saved_at: `${past}T12:30:00Z`, stage: 'earlier-review',
      result: { ...body.snapshots[0].result, revision: 1, collection_guidance: { ...body.snapshots[0].result.collection_guidance, amount_cents: 2500 } },
    })
    savedResult = body.snapshots[0].result
  } else if (path === '/api/staff/intakes/synthetic-intake/checks/older-estimate') {
    body = { snapshots: [{ saved_at: `${past}T12:30:00Z`, stage: 'previsit-complete', patient: patients[5], result: { service_date: past, collection_guidance: { amount_cents: 2500, status: 'estimated' } } }] }
  } else if (path === '/api/doctor/insurance/commercial/cases/saved-estimate/previsit') {
    body = {
      ...savedResult,
      collection_guidance: { ...savedResult.collection_guidance, amount_cents: projectedAmount },
      encounter_summary: { collect_today_cents: projectedAmount },
    }
  } else if (path === '/api/staff/intakes/synthetic-intake/cards') {
    body = { cards: [], message: 'No synthetic card preview.' }
  } else {
    throw new Error(`Unexpected API ${path}`)
  }
  if (path.endsWith('/appointments')) body.appointments = body.appointments.map((visit) => ({ checks: [], ...visit }))
  await route.fulfill({ json: body })
})

try {
  await page.goto(process.env.UI_URL || 'http://127.0.0.1:5182/?staff')
  await page.getByText('eCW visit checks finished for 5 patients.').waitFor()
  assert.equal(appointmentReads, 5)
  assert.match(await page.getByRole('alert').innerText(), /1 patient history was unavailable/)
  const groups = page.locator('.clinical-visit-group')
  assert.equal(await page.locator('.clinical-cohorts').count(), 0)
  assert.equal(await page.getByText('See more').count(), 0)
  assert.equal(await page.locator('.clinical-period-links button').count(), 2)
  assert.equal(await groups.count(), 1)
  assert.deepEqual(await page.locator('.clinical-period-title').allTextContents(), ['Today’s patients'])
  assert.equal(await groups.first().getAttribute('open'), '')
  assert.deepEqual(await groups.first().locator('.clinical-patient-open strong').allTextContents(), [
    'Patient 06',
  ])

  await page.getByRole('button', { name: 'Upcoming patients' }).click()
  assert.equal(await groups.count(), 1)
  const futureGroup = page.locator('section[aria-labelledby="clinical-selected-period-title"] .clinical-visit-group')
  assert.match(await futureGroup.locator('summary').innerText(), /2 patients/)
  await futureGroup.locator('summary').click()
  assert.deepEqual(await futureGroup.locator('.clinical-patient-open strong').allTextContents(), [
    'Patient 02',
    'Patient 01',
  ])
  await futureGroup.locator('.clinical-patient-open').filter({ hasText: 'Patient 01' }).click()
  await page.getByRole('heading', { name: 'Patient 01' }).waitFor()
  await page.locator('.staff-narrow > button').first().click()
  assert.equal(await page.getByRole('heading', { name: 'Upcoming patients' }).count(), 1)
  await page.getByRole('button', { name: 'Today’s patients' }).click()
  await page.getByRole('button', { name: 'Previous patients' }).click()
  const pastGroups = page.locator('section[aria-labelledby="clinical-selected-period-title"] .clinical-visit-group')
  await pastGroups.first().locator('summary').click()
  assert.deepEqual(await pastGroups.first().locator('.clinical-patient-open strong').allTextContents(), [
    'Patient 01',
    'Patient 03',
  ])
  const readsBeforeDetail = dashboardReads
  await pastGroups.first().locator('.clinical-patient-open').filter({ hasText: 'Patient 01' }).click()
  await page.getByRole('heading', { name: 'Patient 01' }).waitFor()
  await page.locator('.staff-narrow > button').first().click()
  assert.equal(await page.getByRole('heading', { name: 'Previous patients' }).count(), 1)
  assert.match(await pastGroups.first().locator('summary').innerText(), /2 patients/)
  assert.equal(dashboardReads, readsBeforeDetail)
  await pastGroups.nth(1).locator('summary').click()
  assert.deepEqual(await pastGroups.nth(1).locator('.clinical-patient-open strong').allTextContents(), [
    'Patient 04',
    'Patient 05',
  ])

  await page.getByRole('button', { name: 'Today’s patients' }).click()
  await page.getByRole('button', { name: 'Upcoming patients' }).click()
  await page.getByLabel('Find a clinical patient').fill('Patient 02')
  assert.match(await futureGroup.locator('summary').innerText(), /1 patient/)
  assert.equal(await groups.count(), 1)
  await page.getByLabel('Find a clinical patient').fill('')
  await page.getByRole('button', { name: 'Today’s patients' }).click()
  await page.locator('.clinical-patient-open').filter({ hasText: 'Patient 06' }).click()
  const summary = page.getByRole('region', { name: 'Latest saved collection estimate' })
  await summary.locator('.visit-patient-bar').getByText('Patient 06').waitFor()
  assert.match(await summary.locator('.visit-context-facts').innerText(), /Synthetic visit review/)
  assert.match(await summary.locator('.visit-context-facts').innerText(), /Example Health/)
  await summary.getByText('$50.00').first().waitFor()
  assert.match(await summary.locator('.arrival-total').innerText(), /(?:US)?\$50\.00/)
  assert.equal(await summary.getByRole('button', { name: 'Attach to this visit' }).count(), 0)
  await summary.getByText('Visit cost and collection').waitFor()
  assert.match(await summary.innerText(), /In network/)
  assert.equal(await summary.getByText('Detailed benefits, insurance & card').locator('..').getAttribute('open'), '')
  assert.equal(await summary.getByText('Saved service plan').locator('..').getAttribute('open'), '')
  assert.match(await summary.getByText('Saved service plan').locator('..').innerText(), /CPT 99204 × 1/)
  assert.equal(await summary.locator('select').count(), 0)
  const readsBeforeEstimate = dashboardReads
  await page.getByRole('button', { name: 'Open insurance review' }).first().click()
  await page.getByRole('button', { name: /Back to/i }).first().click()
  await page.getByRole('heading', { name: 'Patient 06' }).waitFor()
  await page.locator('.staff-narrow > button').first().click()
  assert.equal(dashboardReads, readsBeforeEstimate)
  await page.locator('.clinical-patient-open').filter({ hasText: 'Patient 06' }).click()
  if (process.env.SCREENSHOT_PATH) await page.screenshot({ path: process.env.SCREENSHOT_PATH, fullPage: true })
  assert.equal(await page.locator('.staff-narrow').evaluate((element) => {
    const summary = element.querySelector('.latest-saved-estimate')
    const history = element.querySelector('.patient-appointment-history')
    return [...element.children].indexOf(summary) < [...element.children].indexOf(history)
  }), true)
  assert.equal(await page.locator('.staff-narrow').evaluate((element) => element.lastElementChild?.classList.contains('patient-appointment-history')), true)
  await page.getByText('View saved estimate results', { exact: true }).click()
  await page.getByLabel('Recorded versions').selectOption('1')
  assert.match(await page.locator('.staff-saved-checks').innerText(), /(?:US)?\$25\.00/)
  assert.match(await summary.locator('.arrival-total').innerText(), /(?:US)?\$50\.00/)
  savedAmount = null
  projectedAmount = 3500
  await summary.getByRole('button', { name: 'Refresh' }).click()
  await summary.locator('.arrival-total').getByText('$35.00').waitFor()
  assert.equal(await summary.getByText('$0.00').count(), 0)
  for (const width of [320, 375, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 })
    assert(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      `No overflow at ${width}`,
    )
  }
  useCache = true
  const readsBeforeReload = appointmentReads
  await page.reload()
  await page.getByRole('button', { name: 'Upcoming patients' }).waitFor()
  assert.equal(appointmentReads, readsBeforeReload)
  await page.getByRole('button', { name: 'Previous patients' }).click()
  await page.getByRole('button', { name: 'Visit date page 2' }).click()
  assert.equal(await page.getByRole('button', { name: 'Visit date page 2' }).getAttribute('aria-current'), 'page')
  await page.locator('.clinical-visit-group').first().locator('summary').click()
  await page.locator('.clinical-visit-group').first().locator('.clinical-patient-open').click()
  await page.locator('.staff-narrow > button').first().click()
  assert.equal(await page.getByRole('button', { name: 'Visit date page 2' }).getAttribute('aria-current'), 'page')
  assert.deepEqual(errors, [])
  console.log('PASS: all visit dates, one profile, return navigation, warm cache, responsive layout')
} finally {
  await browser.close()
}
