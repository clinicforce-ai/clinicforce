import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { rolldown } = require('rolldown')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
async function load(path) {
  const bundle = await rolldown({ input: new URL(path, import.meta.url).pathname,
    plugins: [{ name: 'ignore-css', load(id) { if (id.endsWith('.css')) return { code: 'export default {}', moduleType: 'js' } } }] })
  const compiled = await bundle.generate({ format: 'esm' })
  return import(`data:text/javascript;base64,${Buffer.from(compiled.output[0].code).toString('base64')}`)
}
const { deriveCommercialEstimateView } = await load('../frontend/src/features/commercial-estimate/deriveCommercialEstimateView.ts')
const { default: ArrivalAmountSummary } = await load('../frontend/src/features/commercial-estimate/ArrivalAmountSummary.tsx')
const { default: CommercialCostBreakdown } = await load('../frontend/src/features/commercial-estimate/CommercialCostBreakdown.tsx')

const result = {
  payer: 'Aetna', payment_mode: 'insurance', service_date: '2026-12-15',
  identity: { status: 'matched', binding: 'fictional-binding', match_version: 2 },
  benefits: { active: true, service_date_matches: false, network_status: 'unknown',
    network_source: null, source_errors: [] },
  price_range: { complete: false, mode: 'services', lines: [{ id: 'office', code: '99213',
    units: 1, allowed_cents: 12000, rate_resolution: { status: 'RESOLVED' },
    benefits: { active: true, copay_cents: 3000, source_errors: [] } }] },
  aetna_office_planning: { status: 'planning_only', collectable: false,
    allowed_cents: 12000, deductible_applies_cents: 12000, patient_share_cents: 12000,
    deductible_remaining_cents: 20000, oop_remaining_cents: 50000 },
  collection_guidance: { amount_cents: 0, status: 'zero_advance', basis: 'claim_after_service',
    collection_rule: { mode: 'clinic_zero_arrival_default' },
    reason: 'Collect $0 at arrival under the clinic default; final patient share is unknown.',
    services: [] },
  encounter_summary: { collect_today_cents: 0, complete: false,
    estimated_total_patient_min_cents: null },
}
const model = deriveCommercialEstimateView({ result, appointments: [], selectedVisit: '',
  busy: null, error: null, readOnly: true, recovering: false })
assert.equal(model.supportedAmount, true)
assert.equal(model.amount, '$0.00')
const headline = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
  { result, model, partialCopay: null, visibleAmount: true }))
assert.match(headline, /Amount to collect at visit.*\$0\.00/s)
assert.doesNotMatch(headline, /Verify network|\$120\.00/)
const table = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
  { result, model }))
assert.match(table, /Amount to collect at visit.*\$0\.00/s)
console.log('Clinic zero arrival: numeric check-in decision, unresolved patient share')
