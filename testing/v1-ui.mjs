// UI regression only: synthetic case responses, no patient data or live payer calls.
// Run with Vite running and PLAYWRIGHT_MODULE set if Playwright is not local.
import assert from 'node:assert/strict'
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } })
const failures = []
page.on('pageerror', error => failures.push(error.message))
const source = filename => ({ id: filename, filename, sha256: 'a'.repeat(64), observed_at: '2026-09-10T12:00:00Z', document_dates: [{ label: 'Effective Date', value: '2026-01-01', page: 1 }], reference: 'page 1', findings: { pages: [{ page: 1, text: 'Synthetic uploaded document text.' }] } })
const state = {
  id: 'ui-test', created_at: '2026-09-10T12:00:00Z', status: 'review', source_mode: 'documents', next_stage: 'intake',
  ocr: { member_name: 'PAT EXAMPLE', member_id: 'ABC123', confirmed: false, in_network_deductible: '$9,999 UNSOURCED' },
  ocr_evidence: { member_name: { value: 'PAT EXAMPLE', page: 1, quote: 'Member Name: PAT EXAMPLE' }, member_id: { value: 'ABC123', page: 1, quote: 'Member ID: ABC123' } },
  card_source: source('test-card.png'), documents: {}, facts: [], service_lines: [], stage_decisions: [], estimate: null,
}
const codes = [
  { code: '99204', description: 'Uploaded visit description', allowed_cents: 10000 },
  { code: '87635', description: 'Uploaded procedure description', allowed_cents: 4250 },
  { code: '93000', description: 'Uploaded test description', allowed_cents: 5000 },
]
const groups = [{ id: 'visit', category: 'visit', label: 'Office visit alternatives', selection_mode: 'single', description: 'Confirm the documented visit level.', requires_groups: [], codes: ['99204', '99203'] }, { id: 'tests', label: 'Planned tests', selection_mode: 'multiple', description: 'Separate tests from the uploaded care plan.', requires_groups: [], codes: ['87635', '93000'] }]
await page.route('**/api/doctor/insurance/cases**', async route => {
  const request = route.request()
  const path = new URL(request.url()).pathname
  if (path.endsWith('/intake')) {
    assert.equal(request.postDataJSON().member_name, 'PAT EXAMPLE')
    state.ocr.confirmed = true; state.next_stage = 'eligibility'
  } else if (path.includes('/documents/')) {
    const kind = path.split('/').at(-1)
    state.documents[kind] = source(`${kind}.${kind === 'fee_schedule' ? 'csv' : 'pdf'}`)
    if (kind === 'clinical') {
      state.clinical_context = 'New patient. Reports knee pain. No surgery planned.'
      state.visit_type = 'new'
      state.documents.clinical.findings = { clinical_context: state.clinical_context, visit_type: 'new', clinical_excerpts: [{ page: 1, quote: state.clinical_context, category: 'symptoms' }] }
      state.service_lines = []; state.service_groups = []
    }
    if (kind === 'coverage') state.facts = [{ stage: 'network', label: 'Individual deductible remaining', value: '$125.00', citation: { source_filename: 'coverage.pdf', reference: 'page 1', quote: 'Individual deductible remaining: $125.00' } }]
  } else if (path.endsWith('/clinical-context')) {
    assert.equal(request.postDataJSON().clinical_context, 'Entered visit reason stays after upload')
    assert.equal(request.postDataJSON().visit_type, 'new')
    state.service_groups = groups; state.suggestions_reviewed = true
    state.service_lines = [...codes, { code: '99203', description: 'Another uploaded visit level', allowed_cents: 8000 }].map(line => ({ ...line, selected: false, recommendation_reason: 'Synthetic suggestion for UI testing.' }))
  } else if (path.endsWith('/service-lines')) {
    assert.deepEqual(request.postDataJSON().codes, ['99204', '87635', '93000'])
    assert.deepEqual(request.postDataJSON().quantities, {})
    state.service_lines = codes.map(line => ({ ...line, selected: true, confirmed: true, authorization_status: 'not_required', referral_status: 'not_stated' }))
    state.next_stage = 'authorization'; state.stage_decisions.push({ stage: 'service' })
  } else if (path.includes('/stages/')) {
    const stage = path.split('/').at(-1)
    state.stage_decisions.push({ stage })
    state.next_stage = { eligibility: 'network', network: 'service', authorization: null }[stage]
    if (stage === 'authorization') {
      state.status = 'complete'
      state.estimate = { estimate_available: true, allowed_cents: 19250, patient_cents: 13850, plan_cents: 5400,
        collection_action: 'verify_before_collecting', collect_today_cents: null, confidence: 'low', collection_reason: 'The calculation uses the uploaded benefit values.', verification_reasons: ['Confirm referral applicability.'], calculated_at: '2026-09-10T12:30:00Z',
        line_items: codes.map((line, index) => ({ ...line, deductible_applied_cents: [10000, 2500, 0][index], coinsurance_cents: [0, 350, 1000][index], patient_cents: [10000, 2850, 1000][index], plan_cents: [0, 1400, 4000][index] })), disclaimer: 'Synthetic test estimate.' }
    }
  }
  await route.fulfill({ contentType: 'application/json', body: JSON.stringify(state) })
})
const upload = (name, filename) => page.getByLabel(name, { exact: true }).setInputFiles({ name: filename, mimeType: filename.endsWith('csv') ? 'text/csv' : 'image/png', buffer: Buffer.from('Synthetic test upload') })
try {
  await page.goto(process.env.V1_UI_URL || 'http://127.0.0.1:5173')
  await page.evaluate(async () => {
    const { default: React } = await import('/node_modules/.vite/deps/react.js')
    const { default: ReactDOM } = await import('/node_modules/.vite/deps/react-dom_client.js')
    const { default: View } = await import('/src/features/insurance-v1/InsuranceVerification.tsx')
    document.getElementById('root').style.display = 'none'
    const element = document.createElement('div')
    element.style.padding = '32px'
    document.body.append(element)
    ReactDOM.createRoot(element).render(React.createElement(View, { onBack() {} }))
  })
  await page.getByRole('heading', { name: 'Upload the patient’s insurance card' }).waitFor()
  const sidebar = await page.locator('.insurance-stepper').boundingBox()
  const content = await page.locator('.insurance-stage-card').boundingBox()
  assert.ok(sidebar.x + sidebar.width <= content.x, 'Desktop stepper must sit beside the form')
  assert.ok((await page.getByLabel('Insurance card', { exact: true }).getAttribute('accept')).includes('image/png'))
  if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/v1-upload-desktop.png`, fullPage: true })
  await upload('Insurance card', 'test-card.png')
  await page.getByRole('heading', { name: 'Check what was read from the card' }).waitFor()
  assert.equal(await page.getByText('$9,999 UNSOURCED').count(), 0)
  await page.getByRole('button', { name: 'Confirm extracted card fields' }).click()
  await page.getByRole('button', { name: 'Continue to benefits & rates' }).click()
  const next = page.getByRole('button', { name: 'Continue to CPT suggestions' })
  assert.ok(await next.isDisabled())
  await upload('Member benefits · required', 'coverage.png')
  await page.getByText('Uploaded: coverage.pdf').waitFor()
  assert.ok(await page.getByText(/Upload the dated eligibility\/benefits response/).isVisible(), 'Upload instructions must remain after upload')
  assert.ok(await next.isDisabled())
  await upload('Fee schedule · required', 'rates.csv')
  await page.getByText('Uploaded: fee_schedule.csv').waitFor()
  if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/v1-benefits-desktop.png`, fullPage: true })
  await page.setViewportSize({ width: 375, height: 844 })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
  if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/v1-benefits-mobile.png`, fullPage: true })
  await page.setViewportSize({ width: 1440, height: 1050 })
  await next.click()
  await upload('Clinical document · optional if you entered the condition', 'note.png')
  await page.getByText('Uploaded: clinical.pdf').waitFor()
  const context = page.getByLabel('Patient symptoms, condition and planned care')
  assert.equal(await context.inputValue(), 'New patient. Reports knee pain. No surgery planned.')
  assert.equal(await page.getByLabel('Visit type, if known').inputValue(), 'new')
  await context.fill('Entered visit reason stays after upload')
  await upload('Clinical document · optional if you entered the condition', 'replacement.png')
  await page.getByRole('status').filter({ hasText: /Your edited text was kept/ }).waitFor()
  assert.equal(await context.inputValue(), 'Entered visit reason stays after upload')
  await page.getByRole('button', { name: 'Suggest CPT/HCPCS codes', exact: true }).click()
  await page.getByRole('radio', { name: /99203/ }).check()
  await page.getByRole('radio', { name: /99204/ }).check()
  assert.equal(await page.getByRole('radio', { name: /99203/ }).isChecked(), false)
  for (const code of codes.slice(1)) await page.getByRole('checkbox', { name: new RegExp(code.code) }).check()
  if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/v1-cpt-desktop.png`, fullPage: true })
  await page.setViewportSize({ width: 375, height: 844 })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
  if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/v1-cpt-mobile.png`, fullPage: true })
  await page.setViewportSize({ width: 844, height: 375 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
  await page.setViewportSize({ width: 1440, height: 1050 })
  await page.getByRole('button', { name: 'Confirm 3 selected codes' }).click()
  assert.equal(await page.getByLabel('Authorization response · optional', { exact: true }).count(), 0)
  assert.equal(await page.getByText('Referral: Not established by the uploaded documents', { exact: true }).count(), 3)
  await page.getByRole('button', { name: 'Review and show pay estimate' }).click()
  await page.getByRole('heading', { name: 'Your selected codes, item by item' }).waitFor()
  assert.equal(await page.locator('.v1-estimate-items article').count(), 3)
  assert.equal(await page.getByRole('heading', { name: 'Uploaded visit description' }).count(), 1)
  assert.ok(await page.locator('.v1-estimate-total').getByText('$138.50').isVisible())
  assert.equal(await page.locator('.collection-decision').count(), 0)
  assert.ok(await page.getByRole('heading', { name: 'Verify before collecting' }).isVisible())
  assert.equal(await page.locator('.v1-calculation').getAttribute('open'), null)
  assert.ok(await page.getByRole('heading', { name: 'Self-pay option' }).isVisible())
  if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/v1-estimate-desktop.png`, fullPage: true })
  await page.setViewportSize({ width: 375, height: 844 })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
  if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/v1-estimate-mobile.png`, fullPage: true })
  await page.getByRole('button', { name: /Insurance card Completed/ }).click()
  await page.getByRole('heading', { name: 'Check what was read from the card' }).waitFor()
  assert.equal(await page.getByLabel('Replace insurance card', { exact: true }).count(), 0, 'Completed steps are read-only')
  await page.getByRole('button', { name: 'Return to current step' }).click()
  await page.getByRole('heading', { name: 'Your selected codes, item by item' }).waitFor()
  assert.deepEqual(failures, [])
  console.log('PASS: V1 sidebar, guidance, image uploads, source-only card display, context persistence, ordered itemized estimates, completed-step review and mobile layouts.')
} finally { await browser.close() }
