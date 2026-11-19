import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { rolldown } = require('rolldown')
async function load(path) {
  const bundle = await rolldown({ input: new URL(path, import.meta.url).pathname,
    plugins: [{ name: 'ignore-css', load(id) { if (id.endsWith('.css')) return { code: 'export default {}', moduleType: 'js' } } }] })
  const compiled = await bundle.generate({ format: 'esm' })
  return import(`data:text/javascript;base64,${Buffer.from(compiled.output[0].code).toString('base64')}`)
}
const { default: CommercialCostBreakdown } = await load('../frontend/src/features/commercial-estimate/CommercialCostBreakdown.tsx')
const { default: ArrivalAmountSummary } = await load('../frontend/src/features/commercial-estimate/ArrivalAmountSummary.tsx')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')

const result = {
  payer: 'UnitedHealthcare', payment_mode: 'insurance', service_date: '2026-11-19',
  reason: 'Routine follow-up', codes: [{ id: 'office', code: '99213', units: 1 }],
  plan_details: { stedi: { names: ['LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PP'] } },
  benefits: { active: true, service_date_matches: null,
    coverage_evidence: { status: 'ACTIVE', start: '2026-01-01', end: '2026-12-31' },
    network_status: 'unknown',
    has_secondary: false, qmb_status: 'unknown', source_errors: [],
    deductible_annual_cents: 50000, deductible_remaining_cents: 0,
    oop_annual_cents: 200000, oop_remaining_cents: 53235 },
  identity: { binding: 'synthetic-binding', match_version: 2, status: 'matched' },
  recovery: { reason_code: 'same_policy_evidence_conflict' },
  collection_guidance: { amount_cents: null, basis: 'missing_collection_inputs' },
  temporary_rate_reference: { complete: true, source: 'Synthetic BCBS Texas rate',
    message: 'Separate clinic reference, not a UHC allowance.',
    lines: [{ id: 'office', code: '99213', units: 1, unit_reference_cents: 7615 }] },
  price_range: { mode: 'services', lines: [{ id: 'office', code: '99213', units: 1,
    allowed_cents: 9633, allowed_basis: 'medicare_fee_schedule',
    issues: [],
    rate_resolution: { status: 'RESOLVED' }, benefits: { active: true,
      network_independent: true, copay_cents: 0, coinsurance_percent: '20.0',
      deductible_remaining_cents: 0, oop_remaining_cents: 53235,
      source_errors: [] } }] },
}
const model = { cash: false, calculating: false, previousEstimate: false, supportedAmount: false,
  networkLabel: 'Patient plan not matched', amountText: 'Insured amount unavailable' }
const table = (input, view = model) => renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
  { result: input, model: view }))
const html = table(result)
assert.match(html, /Not confirmed by UHC.*\$96\.33 clinic Medicare fee reference/)
assert.match(html, /Estimated patient share.*\$19\.27 provisional/s)
assert.match(html, /Amount to collect at visit.*\$19\.27 provisional/s)
assert.match(html, /Reconcile the policy conflict/)
for (const value of ['$500.00 annual', '$0.00 remaining', '$2,000.00 annual',
  '$532.35 remaining', '$76.15']) assert.ok(html.includes(value), `Missing ${value}`)
const headline = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
  { result, model, partialCopay: null, visibleAmount: false }))
assert.match(headline, /\$19\.27/)
assert.match(table({ ...result, encounter_summary: { payments_verified_cents: 500 } }), /\$14\.27 provisional/)
assert.doesNotMatch(table({ ...result, billing_protection: { status: 'qmb' } }), /\$19\.27/)
assert.doesNotMatch(table(result, { ...model, previousEstimate: true }), /\$19\.27/)
assert.doesNotMatch(table({ ...result, benefits: { ...result.benefits, source_errors: [{ kind: 'invalid' }] } }), /\$19\.27/)
assert.doesNotMatch(table({ ...result, recovery: { reason_code: 'multiple_active_policies' } }), /\$19\.27/)
console.log('UHC LPPO office provisional coinsurance: $19.27 from $96.33 reference')
