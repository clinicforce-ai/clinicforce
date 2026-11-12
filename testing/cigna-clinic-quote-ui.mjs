import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { rolldown } = require('rolldown')
const bundle = await rolldown({ input: new URL('../frontend/src/features/commercial-estimate/CommercialCostBreakdown.tsx', import.meta.url).pathname,
  plugins: [{ name: 'ignore-css', load(id) { if (id.endsWith('.css')) return { code: 'export default {}', moduleType: 'js' } } }] })
const compiled = await bundle.generate({ format: 'esm' })
const { default: CommercialCostBreakdown } = await import(`data:text/javascript;base64,${Buffer.from(compiled.output[0].code).toString('base64')}`)
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const result = {
  payer: 'Cigna', service_date: '2026-11-12', reason: 'Synthetic dizziness visit',
  identity: { status: 'matched', match_version: 2, binding: 'synthetic' },
  benefits: { active: true, service_date_matches: true, network_status: 'in_network',
    network_source: 'payer_participation_message', deductible_remaining_cents: 335505,
    oop_remaining_cents: 335505, coinsurance_percent: '0' },
  codes: [{ id: 'office', code: '99203', units: 1, modifiers: [] },
    { id: 'maneuver', code: '95992', units: 1, modifiers: [] }],
  price_range: { mode: 'services', lines: ['99203', '95992'].map((code, index) => ({
    id: index ? 'maneuver' : 'office', code, units: 1, modifiers: [],
    benefits: { active: true, network_status: 'in_network', deductible_remaining_cents: 335505,
      oop_remaining_cents: 335505, coinsurance_percent: '0' } })) },
  temporary_rate_reference: { complete: true, source: 'NMCT Clinical Library Blue Cross fee schedule',
    lines: [{ id: 'office', code: '99203', units: 1, modifiers: [], unit_reference_cents: 10117,
      source_reference: 'library:synthetic' },
      { id: 'maneuver', code: '95992', units: 1, modifiers: [], unit_reference_cents: 3412,
        source_reference: 'published:synthetic' }] },
  fee_proxy_estimate: { basis: 'cigna_clinic_fee_quote', status: 'provisional',
    reference_total_cents: 13529, deductible_cents: 13529, coinsurance_percent: '0',
    coinsurance_cents: 0, copay_cents: 0, copay_unconfirmed: true, oop_reduction_cents: 0,
    oop_remaining_cents: 335505, patient_share_cents: 13529,
    lines: [{ code: '99203', units: 1, reference_cents: 10117, deductible_cents: 10117,
      coinsurance_base_cents: 0, coinsurance_cents: 0, source: 'BCBS Texas clinic fee proxy' },
      { code: '95992', units: 1, reference_cents: 3412, deductible_cents: 3412,
        coinsurance_base_cents: 0, coinsurance_cents: 0, source: 'BCBS Texas clinic fee proxy' }],
    rate_source: 'Clinic Cigna contract and BCBS Texas fee schedule',
    deductible_source: 'Current individual in-network medical benefit',
    message: 'Synthetic refundable clinic quote.' },
  collection_guidance: { amount_cents: 13529, status: 'estimated', basis: 'cigna_clinic_fee_quote',
    reason: 'Clinic-directed refundable advance; not a Cigna Cost of Care Estimator result.',
    collection_rule: { mode: 'cigna_clinic_fee_quote' } },
}
const model = { cash: false, calculating: false, previousEstimate: false, supportedAmount: false,
  networkLabel: 'In network', estimatedShare: null, amountText: '$135.29', provisionalAmount: null }
const html = renderToStaticMarkup(React.createElement(CommercialCostBreakdown, { result, model }))
for (const expected of ['CPT 99203', '$101.17 BCBS Texas reference', 'CPT 95992',
  '$34.12 BCBS Texas reference', '$135.29',
  'Amount to collect at visit · refundable clinic quote', '0% · $0.00 after deductible']) {
  assert.ok(html.includes(expected), `Missing ${expected}`)
}
assert.ok(html.includes('BCBS Texas proxy used for other CPTs'))
assert.ok(!html.includes('$101.17 Cigna contract allowance'))
console.log('Cigna clinic quote UI: disclosed two BCBS proxy lines and $135.29 refundable advance')
