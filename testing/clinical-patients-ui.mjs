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
  } else if (path === '/api/staff/patients/dashboard') {
    body = {
      patients,
      counts: { all: 6, accounts_with_cards: 3, accounts_without_cards: 3, card_only: 0, other: 0 },
    }
  } else if (path.endsWith('/appointments')) {
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
                ? [{ id: 'today-6', from_ecw: true, status: 'booked', date: today }]
              : [],
    }
  } else {
    throw new Error(`Unexpected API ${path}`)
  }
  await route.fulfill({ json: body })
})

try {
  await page.goto(process.env.UI_URL || 'http://127.0.0.1:5182/?staff')
  await page.getByText('eCW visit checks finished for 5 patients.').waitFor()
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
  await page.getByRole('button', { name: 'Today’s patients' }).click()
  await page.getByRole('button', { name: 'Previous patients' }).click()
  const pastGroups = page.locator('section[aria-labelledby="clinical-selected-period-title"] .clinical-visit-group')
  await pastGroups.first().locator('summary').click()
  assert.deepEqual(await pastGroups.first().locator('.clinical-patient-open strong').allTextContents(), [
    'Patient 03',
  ])
  await pastGroups.nth(1).locator('summary').click()
  assert.deepEqual(await pastGroups.nth(1).locator('.clinical-patient-open strong').allTextContents(), [
    'Patient 04',
    'Patient 05',
  ])

  await page.getByRole('button', { name: 'Today’s patients' }).click()
  await page.getByRole('button', { name: 'Upcoming patients' }).click()
  await page.getByLabel('Find a clinical patient').fill('Patient 02')
  await page.getByRole('button', { name: 'Upcoming patients' }).click()
  assert.match(await futureGroup.locator('summary').innerText(), /1 patient/)
  assert.equal(await groups.count(), 1)
  await page.getByLabel('Find a clinical patient').fill('')
  for (const width of [320, 375, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 })
    assert(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
      `No overflow at ${width}`,
    )
  }
  assert.deepEqual(errors, [])
  console.log('PASS: appointment time order, date groups, search, responsive layout')
} finally {
  await browser.close()
}
