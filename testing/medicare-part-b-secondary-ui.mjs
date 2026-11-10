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
const { default: ArrivalAmountSummary } = await load('../frontend/src/features/commercial-estimate/ArrivalAmountSummary.tsx')
const { default: CommercialCostBreakdown } = await load('../frontend/src/features/commercial-estimate/CommercialCostBreakdown.tsx')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')

const result = {
  payer: 'Blue Cross Blue Shield Tx', service_date: '2026-11-17',
  benefits: { active: true, has_secondary: true, network_status: 'unknown',
    copay_cents: 6000, deductible_annual_cents: 120000,
    deductible_remaining_cents: 120000, oop_annual_cents: 450000,
    oop_remaining_cents: 443428, medicare_part_b_secondary: {
      status: 'reported', payer: 'Original Medicare Part B', evidence_paths: ['synthetic-row'],
    }, reported_options: [] },
  identity: { status: 'warning', match_version: 2, binding: 'synthetic',
    reconciliation_rule: 'payer_matched_patient' },
  recovery: { reason_code: 'multiple_active_policies', active_policy_count: 3 },
  price_range: { mode: 'services', complete: false, lines: [{ id: 'office', code: '99203',
    units: 1, allowed_cents: null, benefits: { active: true, copay_cents: 6000,
      deductible_applies: false } }] },
  collection_guidance: { amount_cents: 0, status: 'zero_advance', basis: 'claim_after_service',
    reason: 'Collect no office copay at check-in; submit the claims.',
    collection_rule: { mode: 'cms_msp_part_b_deferral', reference: 'CMS MSP manual' } },
  encounter_summary: { collect_today_cents: 0, complete: false },
}
const model = { cash: false, previousEstimate: false, calculating: false,
  supportedAmount: false, amountText: 'Insured amount unavailable',
  networkLabel: 'Network not determined', cashPrice: '$250.00' }
const row = collectionAtVisitRow(result, model, {
  combinedPlan: null, feeProxy: null, aetnaPlanning: null, aetnaCopayPlanning: null,
  officeReferenceCopay: null, humanaReferenceCopay: null, partialOfficeCopay: null,
  pendingAetnaCopay: null, priorDateAetnaCopay: null, pendingBluecardCopay: null,
  cardPlanningCopay: null, cignaCard: null, automaticCashFallback: false,
  cignaFeeProxy: false, proxyFormula: undefined,
})
assert.equal(row.label, 'Amount to collect at visit')
assert.equal(row.value, '$0.00')
assert.match(row.detail, /no office copay/i)
const summary = renderToStaticMarkup(React.createElement(ArrivalAmountSummary, {
  model, result, partialCopay: null, visibleAmount: true,
}))
assert.match(summary, /Amount to collect at visit/)
assert.match(summary, /\$0\.00/)
assert.doesNotMatch(summary, /Confirm payer order/)
const table = renderToStaticMarkup(React.createElement(CommercialCostBreakdown, { model, result }))
assert.match(table, /Amount to collect at visit/)
assert.match(table, /\$0\.00/)
assert.match(table, /\$60\.00/)
assert.match(table, /\$1,200\.00/)
assert.match(table, /\$4,434\.28/)
