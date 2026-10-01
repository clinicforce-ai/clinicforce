// Synthetic browser regression; every API request is intercepted.
import assert from 'node:assert/strict'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
const errors = []
page.on('pageerror', error => errors.push(error.message))
let patients = Array.from({ length: 23 }, (_, index) => ({
  id: String(index + 1), clinic_patient_id: String(index + 1),
  given_name: 'Patient', family_name: String(index + 1).padStart(2, '0'),
  birth_date: '2000-01-01', account_status: 'portal',
  card_status: index % 2 ? 'missing' : 'received',
  identity_status: 'confirmed', intakes: [], intake_ids: [],
}))
await page.route(url => url.pathname.startsWith('/api/'), async route => {
  const path = new URL(route.request().url()).pathname
  let body
  if (path === '/api/staff/session') body = { staff: { id: 'staff', practice_id: 'clinic', practice_name: 'Example Clinic', display_name: 'Clinician', role: 'admin' } }
  else if (path === '/api/staff/intakes/search') body = { intakes: [], has_more: false }
  else if (path === '/api/staff/patients/dashboard') body = { patients, counts: { all: patients.length, accounts_with_cards: patients.filter(p => p.card_status === 'received').length, accounts_without_cards: patients.filter(p => p.card_status === 'missing').length, card_only: 0, other: 0 } }
  else if (path.endsWith('/appointments')) body = { appointments: [], ecw_status: 'complete' }
  else if (path === '/api/staff/patients/8' && route.request().method() === 'DELETE') { patients = patients.filter(p => p.id !== '8'); body = { cleanup_pending: false } }
  else throw new Error('Unexpected API ' + path)
  await route.fulfill({ json: body })
})
const rows = page.locator('.clinical-patient-row')
const pagination = page.getByRole('navigation', { name: 'Patient pages' })
async function assertPage(number, first, count) {
  await page.waitForFunction(({ number, first, count }) => {
    const rows = document.querySelectorAll('.clinical-patient-row')
    return rows.length === count && rows[0]?.textContent.includes(first) && document.querySelector('[aria-current="page"]')?.textContent === String(number)
  }, { number, first, count })
  assert.equal(await rows.count(), count)
}
try {
  await page.goto(process.env.UI_URL || 'http://127.0.0.1:5182/?staff')
  await assertPage(1, 'Patient 01', 7)
  assert.equal(await pagination.getByRole('button').count(), 6, 'Four numbered pages and two arrows')
  assert(await pagination.getByRole('button', { name: 'Previous patient page' }).isDisabled())
  await pagination.getByRole('button', { name: 'Next patient page' }).click()
  await assertPage(2, 'Patient 08', 7)
  await pagination.getByRole('button', { name: 'Patient page 4', exact: true }).click()
  await assertPage(4, 'Patient 22', 2)
  assert(await pagination.getByRole('button', { name: 'Next patient page' }).isDisabled())
  await pagination.getByRole('button', { name: 'Previous patient page' }).click()
  await assertPage(3, 'Patient 15', 7)
  await page.getByLabel('Find a clinical patient').fill('Patient 23')
  await assertPage(1, 'Patient 23', 1)
  await page.getByLabel('Find a clinical patient').fill('No match')
  await page.getByRole('heading', { name: 'No matching patients' }).waitFor()
  assert.equal(await pagination.count(), 0)
  await page.getByLabel('Find a clinical patient').fill('')
  await pagination.getByRole('button', { name: 'Patient page 4', exact: true }).click()
  await page.getByLabel('Insurance card status').selectOption('missing')
  await assertPage(1, 'Patient 02', 7)
  assert.equal(await page.locator('.clinical-patient-open .staff-status').allTextContents().then(values => values.every(value => value === 'Card missing')), true)
  await page.getByLabel('Insurance card status').selectOption('')
  await pagination.getByRole('button', { name: 'Patient page 4', exact: true }).click()
  await page.getByRole('button', { name: /Accounts with cards/ }).click()
  await assertPage(1, 'Patient 01', 7)
  await page.getByRole('button', { name: /All patients/ }).click()
  await pagination.getByRole('button', { name: 'Patient page 2', exact: true }).click()
  await page.getByRole('button', { name: /Patient 08 Date of birth/ }).click()
  await page.getByRole('heading', { name: 'Patient 08' }).waitFor()
  await page.getByLabel('Staff insurance workspace').getByRole('button', { name: 'Clinical patients', exact: true }).click()
  await assertPage(2, 'Patient 08', 7)
  for (const width of [320, 375, 768, 1440]) {
    await page.setViewportSize({ width, height: 1000 })
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `No overflow at ${width}`)
  }
  patients = patients.slice(0, 8)
  await page.getByRole('button', { name: 'Refresh patients', exact: true }).click()
  await assertPage(2, 'Patient 08', 1)
  await page.getByRole('button', { name: /Patient 08 Date of birth/ }).click()
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: 'Delete patient', exact: true }).click()
  await assertPage(1, 'Patient 01', 7)
  assert(await pagination.getByRole('button', { name: 'Next patient page' }).isDisabled())
  patients = []
  await page.getByRole('button', { name: 'Refresh patients', exact: true }).click()
  await page.getByRole('heading', { name: 'No clinic patients yet' }).waitFor()
  assert.equal(await pagination.count(), 0)
  assert.deepEqual(errors, [])
  console.log('PASS: seven-row pages, numbered pages/arrows, boundaries, filter resets, detail return, responsive layout, deletion clamp, empty list')
} finally { await browser.close() }
