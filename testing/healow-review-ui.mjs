// Synthetic patient data only; intercept every API request.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 }, timezoneId: 'America/Los_Angeles', locale: 'en-US' })
const errors = []
page.on('pageerror', error => errors.push(error.message))
const session = { patient_id: 'synthetic-review', documents: [], records: [], storage_provider: 's3', drive_connected: false, drive_pending_import_count: 0, drive_sync_message: null, drive_access_upgrade_required: false }
const resources = [
  { resourceType: 'Patient', id: 'p1', name: [{ given: ['Test'], family: 'Patient' }], birthDate: '1986-11-23', gender: 'male' },
  ...Array.from({ length: 24 }, (_, id) => ({ resourceType: 'Condition', id: String(id), code: { text: `Example diagnosis ${id + 1}` }, category: [{ text: 'Problem List Item' }] })),
  { resourceType: 'Coverage', id: 'coverage', payor: [], status: 'active' },
  { resourceType: 'ServiceRequest', id: 'order', code: { text: 'Vitamin B12' }, status: 'active' },
  { resourceType: 'Encounter', id: 'visit', status: 'cancelled', type: [{ text: 'Office visit' }], period: { start: '2026-12-22' } },
  { resourceType: 'CarePlan', id: 'plan', status: 'active', text: { div: '<div xmlns="http://www.w3.org/1999/xhtml"><p>Follow up in 4 weeks.</p><script>window.narrativeExecuted=true</script><img src="https://untrusted.invalid/image" onerror="window.narrativeExecuted=true" /></div>' } },
  { resourceType: 'DocumentReference', id: 'doc', type: { text: 'CCD Document', coding: [{ display: 'Consultation note' }] } },
  { resourceType: 'DocumentReference', id: 'doc', type: { text: 'CCD Document' } },
  { resourceType: 'Binary', id: 'binary' },
]
let record = { source: { practice_code: 'TESTAA', organization_name: 'Test Practice', retrieved_at: '2026-09-30T15:00:00Z' }, availability: { Patient: 'ok', Condition: 'ok', Coverage: 'ok', ServiceRequest: 'ok', Encounter: 'ok', CarePlan: 'ok', DocumentReference: 'ok', AllergyIntolerance: 'empty', Observation: 'empty', Provenance: 'partial' }, bundle: { resourceType: 'Bundle', entry: resources.map(resource => ({ resource })) } }
let fetches = 0
let imports = 0
let remoteNarrativeRequests = 0
page.on('request', request => { if (request.url().includes('untrusted.invalid')) remoteNarrativeRequests++ })
await page.route(url => url.pathname.startsWith('/api/'), async route => {
  const path = new URL(route.request().url()).pathname
  let body
  if (path === '/api/patient/session') body = session
  else if (path === '/api/patient/profile') body = { given_name: 'Test', family_name: 'Patient', birth_date: '1986-11-23', administrative_gender: 'male', suggested_given_name: null, suggested_family_name: null }
  else if (path === '/api/patient/idme/status') body = { enabled: false, linked: false }
  else if (path === '/api/patient/ecw/record') { fetches++; body = record }
  else if (path === '/api/patient/ecw/import') { imports++; body = session }
  else throw new Error(`Unexpected API ${path}`)
  await route.fulfill({ json: body })
})
const review = page.locator('.healow-review')
async function connect() {
  await page.evaluate(() => window.postMessage({ type: 'clinicforce:ecw-connected' }, window.location.origin))
  await review.waitFor()
}
try {
  await page.goto(process.env.UI_URL || 'http://127.0.0.1:5187/?patient')
  await page.getByRole('button', { name: 'Care connections', exact: true }).click()
  await connect()
  assert.match(await review.locator('.healow-patient').innerText(), /Nov 23, 1986/)
  assert.equal(await review.locator('.healow-load-notice').count(), 0, 'Empty or technical categories are not permission failures')
  assert.equal(await review.getByRole('heading', { name: 'Example diagnosis 24', exact: true }).count(), 1, 'Do not silently stop at 20 records')
  assert.equal(await review.getByText('Clinical data retrieved', { exact: true }).count(), 0)
  await review.locator('summary').filter({ hasText: 'Documents' }).click()
  assert.equal(await review.getByRole('heading', { name: 'Consultation note', exact: true }).count(), 1)
  const save = review.getByRole('button', { name: /^Save / })
  assert(await save.isDisabled())
  await review.getByLabel('I recognize this patient and have reviewed the records I am saving.').check()
  assert(await save.isEnabled())
  assert.equal(imports, 0, 'Reviewing must not import anything')
  await review.locator('summary').filter({ hasText: 'Care plans & care team' }).click()
  assert.match(await review.locator('.healow-plan-notes').innerText(), /Follow up in 4 weeks/)
  assert.equal(await page.evaluate(() => window.narrativeExecuted), undefined)
  assert.equal(remoteNarrativeRequests, 0)
  await review.locator('summary').filter({ hasText: 'Orders & referrals' }).click()
  assert(await review.getByText('Requests for tests, procedures, or referrals. An order is not a completed test result.', { exact: true }).isVisible())
  await review.locator('summary').filter({ hasText: 'Insurance' }).click()
  assert(await review.getByRole('heading', { name: 'Insurance coverage', exact: true }).isVisible())
  for (const width of [320, 375, 768, 1280]) {
    await page.setViewportSize({ width, height: 1000 })
    const overflow = await page.evaluate(() => ({ width: window.innerWidth, scroll: document.documentElement.scrollWidth, elements: [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > window.innerWidth + 1).slice(0, 12).map(e => ({ tag: e.tagName, class: e.className, right: e.getBoundingClientRect().right })) }))
    assert(overflow.scroll <= overflow.width, `No horizontal overflow at ${width}px: ${JSON.stringify(overflow)}`)
  }
  record = { ...record, source: { ...record.source, retrieved_at: '2026-09-30T15:01:00Z' }, availability: { ...record.availability, Observation: 'partial' } }
  await review.getByRole('button', { name: 'Reload records' }).click()
  await review.getByText('Some categories need another try', { exact: true }).waitFor()
  assert.match(await review.locator('.healow-load-notice').innerText(), /does not mean/)
  assert(await save.isDisabled(), 'Refreshed records need fresh review')
  record = { ...record, source: { ...record.source, retrieved_at: '2026-09-30T15:02:00Z' }, availability: { ...record.availability, Observation: 'forbidden' } }
  await review.getByRole('button', { name: 'Reload records' }).click()
  await review.getByText('Healow denied access to at least one category. You can reconnect to review the sharing permissions.', { exact: true }).waitFor()
  assert.equal(fetches, 3)
  await review.getByLabel('I recognize this patient and have reviewed the records I am saving.').check()
  await save.click()
  await page.getByText('Your confirmed eClinicalWorks data was imported with its eCW/healow source label. The live connection has been closed.', { exact: true }).waitFor()
  assert.equal(imports, 1)
  if (process.env.HEALOW_SANDBOX_FIXTURE) {
    record = JSON.parse(readFileSync(process.env.HEALOW_SANDBOX_FIXTURE, 'utf8'))
    await connect()
    assert.equal(await review.locator('.healow-load-notice').count(), 0)
    assert.equal(await review.locator('.healow-records > li').count(), 98)
  }
  if (process.env.SCREENSHOT_DIR) {
    await review.locator('.healow-category').evaluateAll(nodes => nodes.forEach(node => { node.open = false }))
    await review.evaluate(node => node.scrollIntoView({ block: 'start' }))
    await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/healow-review-desktop.png`, fullPage: false })
    await page.setViewportSize({ width: 375, height: 900 })
    await review.evaluate(node => node.scrollIntoView({ block: 'start' }))
    await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/healow-review-mobile.png`, fullPage: false })
  }
  assert.deepEqual(errors, [])
  console.log('PASS: identity/date, all records, empty/denied/partial categories, safe notes, orders, responsive layout, refresh, explicit import.')
} finally { await browser.close() }
