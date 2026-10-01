// Synthetic clinic data only; intercept every API request.
import assert from 'node:assert/strict'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true })
const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } })
const page = await context.newPage()
const errors = []
page.on('pageerror', error => errors.push(error.message))
let role = 'admin'
let failList = false
let documents = ['contract', 'fee_schedule', 'self_pay', 'era_835', 'plan_matrix'].map((kind, index) => ({
  id: `document-${index}`, content_type: kind === 'contract' ? 'application/pdf' : 'text/csv', kind, label: ['Insurance contract', 'Office fee schedule', 'Self-pay rates', 'Final ERA', 'NMCT payer plan matrix'][index],
  filename: kind === 'contract' ? 'contract.pdf' : kind === 'plan_matrix' ? 'clinic-plans.xlsx' : kind === 'self_pay' ? 'clinic-cash.docx' : `${kind}.csv`, payer: 'Example payer',
  estimator_uses: kind === 'self_pay' ? [{ id: 'cash-version', type: 'cash_rates', status: 'active', payer: '', effective_from: '2026-01-01' }] : [],
  created_at: '2026-10-01T10:00:00Z', effective_from: '2026-01-01', effective_to: '2026-12-31',
}))
await context.route(url => url.pathname.startsWith('/api/'), async route => {
  const url = new URL(route.request().url())
  if (url.pathname.endsWith('/document')) {
    assert.equal(url.searchParams.get('preview'), 'true')
    return route.fulfill({ contentType: 'text/plain', body: 'CPT,Cash Price\n99213,100' })
  }
  let body
  if (url.pathname === '/api/staff/session') body = { staff: { id: 'test-staff', practice_id: 'test-clinic', practice_name: 'Example Clinic', display_name: 'Test Staff', role } }
  else if (url.pathname === '/api/staff/intakes/search') body = { intakes: [], has_more: false }
  else if (url.pathname === '/api/staff/patients/dashboard') body = { patients: [], counts: {} }
  else if (url.pathname === '/api/staff/library') {
    if (route.request().method() === 'POST') {
      const uploaded = { ...documents[0], id: 'new-document', label: 'New clinic contract' }
      documents = [uploaded, ...documents]
      return route.fulfill({ status: 201, json: uploaded })
    }
    if (failList) return route.fulfill({ status: 503, json: { detail: 'Unable to load clinic documents.' } })
    body = { documents }
  } else throw new Error(`Unexpected API ${url.pathname}`)
  return route.fulfill({ json: body })
})
const openLibrary = async () => {
  await page.goto(process.env.UI_URL || 'http://127.0.0.1:5189/?staff')
  await page.getByRole('button', { name: 'Clinic library', exact: true }).click()
  await page.getByRole('heading', { name: 'Saved clinic documents' }).waitFor()
}
try {
  await openLibrary()
  await page.getByText('5 documents', { exact: true }).waitFor()
  assert(await page.getByRole('heading', { name: 'Add a clinic document' }).isVisible())
  assert(await page.getByText('Active in estimator · Clinic self-pay prices', { exact: true }).isVisible())
  assert.match(await page.locator('main').innerText(), /Clinic fees and self-pay prices are shared across payers/)
  assert.equal(await page.getByRole('link', { name: 'Read extracted text', exact: true }).getAttribute('href'), '/api/staff/library/document-0/text')
  for (const document of documents) {
    const link = page.getByRole('link', { name: `Open ${document.label} in a new tab` })
    assert(await link.isVisible())
    assert.equal(await link.getAttribute('href'), `/api/staff/library/${document.id}/document?preview=true`)
    assert.equal(await link.getAttribute('rel'), 'noopener noreferrer')
  }
  const popupPromise = context.waitForEvent('page')
  await page.getByRole('link', { name: 'Open Self-pay rates in a new tab' }).click()
  const popup = await popupPromise
  await popup.waitForLoadState()
  assert.match(await popup.locator('body').innerText(), /99213,100/)
  await popup.close()
  await page.getByLabel('Document type').selectOption('contract')
  await page.getByLabel('Label *', { exact: true }).fill('New clinic contract')
  await page.getByLabel(/^File \*/).setInputFiles({ name: 'contract.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF synthetic') })
  await page.getByRole('button', { name: 'Save clinic document' }).click()
  await page.getByRole('link', { name: 'Open New clinic contract in a new tab' }).waitFor()
  assert(await page.getByText('6 documents', { exact: true }).isVisible())
  for (const width of [375, 812, 1280]) {
    await page.setViewportSize({ width, height: width === 812 ? 375 : 1000 })
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `No horizontal overflow at ${width}px`)
    if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/clinic-library-${width}.png`, fullPage: true })
  }
  role = 'staff'
  await openLibrary()
  await page.getByText('6 documents', { exact: true }).waitFor()
  assert.equal(await page.getByRole('heading', { name: 'Add a clinic document' }).count(), 0)
  failList = true
  await openLibrary()
  await page.getByRole('alert').waitFor()
  assert.equal(await page.getByText('No clinic documents yet', { exact: true }).count(), 0)
  failList = false
  documents = []
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await page.getByText('No clinic documents yet', { exact: true }).waitFor()
  assert.deepEqual(errors, [])
  console.log('PASS: document list, open in new tab, upload refresh, responsive layout, staff access, errors and empty state.')
} finally { await browser.close() }
