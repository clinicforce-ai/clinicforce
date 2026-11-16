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
const { collectionAtVisitRow } = await load('../frontend/src/features/commercial-estimate/collectionAtVisitRow.ts')

const result = {
  payer: 'Cigna', reason: 'BTX', service_date: '2026-11-17',
  service_plan: { kind: 'botox', recommended_ids: [] },
  identity: { status: 'warning', binding: 'synthetic-member', match_version: 2,
    reconciliation_rule: 'cigna_member_suffix' },
  benefits: {
    active: true, service_date_matches: true, network_status: 'unknown',
    deductible_remaining_cents: 0, oop_remaining_cents: 90000,
    reported_options: [
      { kind: 'copay', service_type_code: '98', coverage_level: 'INDIVIDUAL',
        network: 'IN_NETWORK', places_of_service: ['11'], payer_note: 'Specialist Visit or Evaluation',
        amount_cents: 4000 },
      { kind: 'coinsurance', service_type_code: '98', coverage_level: 'INDIVIDUAL',
        network: 'IN_NETWORK', places_of_service: [], percent: '0' },
    ],
  },
  network_evidence: { place_of_service: '11' },
  recovery: { reason_code: 'multiple_active_policies', active_policy_count: 2 },
  collection_guidance: { amount_cents: null, basis: 'missing_collection_inputs' },
  price_range: { mode: 'services', lines: [] },
}
const model = { cash: false, calculating: false, previousEstimate: false, supportedAmount: false }
const values = {
  combinedPlan: null, feeProxy: null, aetnaPlanning: null, aetnaCopayPlanning: null,
  officeReferenceCopay: null, humanaReferenceCopay: null, partialOfficeCopay: null,
  pendingAetnaCopay: null, priorDateAetnaCopay: null, pendingBluecardCopay: null,
  cardPlanningCopay: null, cignaCard: null, automaticCashFallback: false,
  cignaFeeProxy: false, proxyFormula: undefined, bluecardDeductible: null,
}
const row = collectionAtVisitRow(result, model, values)
assert.match(row.value, /\$40\.00/)
assert.match(row.label, /pending/i)
assert.match(row.detail, /\$900\.00/)
assert.match(row.detail, /0%/)
assert.match(row.detail, /BTX/i)
assert.equal(result.collection_guidance.amount_cents, null)
for (const changed of [
  { benefits: { ...result.benefits, oop_remaining_cents: 0 } },
  { benefits: { ...result.benefits, deductible_remaining_cents: 5000 } },
  { benefits: { ...result.benefits, network_status: 'out_of_network' } },
  { payer: 'United Healthcare' },
]) {
  const blocked = collectionAtVisitRow({ ...result, ...changed }, model, values)
  assert.doesNotMatch(blocked.value, /\$40\.00 conditional/)
}
const { default: CommercialCostBreakdown } = await load('../frontend/src/features/commercial-estimate/CommercialCostBreakdown.tsx')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const html = renderToStaticMarkup(React.createElement(CommercialCostBreakdown, { result, model }))
assert.match(html, /\$40\.00 conditional specialist office visit/)
assert.match(html, /Conditional office-only share/)
assert.match(html, /\$40\.00 specialist office scenario; full BTX share unknown/)
assert.match(html, /\$900\.00/)
console.log('Cigna BTX office-only scenario remains visible and noncollectible')
