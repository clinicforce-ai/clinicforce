// Synthetic browser regression; every API request is intercepted.
import assert from 'node:assert/strict'

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true })
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
const patients = Array.from({ length: 5 }, (_, index) => ({
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
      counts: { all: 5, accounts_with_cards: 3, accounts_without_cards: 2, card_only: 0, other: 0 },
    }
  } else if (path.endsWith('/appointments')) {
    const patientId = path.split('/')[4]
    body = {
      ecw_status: patientId === '5' ? 'incomplete' : 'complete',
      appointments:
        patientId === '1'
          ? [
              { id: 'future-1', from_ecw: true, status: 'planned', date: future },
              { id: 'past-1', from_ecw: true, status: 'finished', date: past },
            ]
          : patientId === '2'
            ? [{ id: 'future-2', from_ecw: true, status: 'booked', date: future }]
            : patientId === '3'
              ? [{ id: 'past-planned', from_ecw: true, status: 'planned', date: past }]
              : [],
    }
  } else {
    throw new Error(`Unexpected API ${path}`)
  }
  await route.fulfill({ json: body })
})

try {
  await page.goto(process.env.UI_URL || 'http://127.0.0.1:5182/?staff')
  await page.getByText('eCW visit checks finished for 4 patients.').waitFor()
  const groups = page.locator('.clinical-visit-group')
  assert.equal(await groups.count(), 5)
  const futureGroup = groups.filter({ has: page.locator('.clinical-visit-upcoming') })
  assert.equal(await futureGroup.count(), 1)
  assert.equal(await groups.first().locator('.clinical-visit-upcoming').count(), 1)
  assert.match(await futureGroup.locator('summary').innerText(), /2 patients/)
  await futureGroup.locator('summary').click()
  assert.deepEqual(await futureGroup.locator('.clinical-patient-open strong').allTextContents(), [
    'Patient 01',
    'Patient 02',
  ])
  await groups.nth(1).locator('summary').click()
  assert.deepEqual(await groups.nth(1).locator('.clinical-patient-open strong').allTextContents(), [
    'Patient 01',
  ])
  assert.equal(await groups.filter({ hasText: 'No upcoming or completed eCW visit' }).count(), 1)
  assert.equal(await groups.filter({ hasText: 'eCW visits unavailable' }).count(), 1)
  assert.equal(await groups.filter({ hasText: 'No confirmed eCW link' }).count(), 1)

  await page.getByLabel('Find a clinical patient').fill('Patient 02')
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
  console.log('PASS: future and completed eCW date groups, unavailable states, search, responsive layout')
} finally {
  await browser.close()
}
