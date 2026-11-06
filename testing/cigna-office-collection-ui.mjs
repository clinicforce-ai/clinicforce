import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { rolldown } = require('rolldown')
const bundle = await rolldown({
  input: new URL('../frontend/src/features/commercial-estimate/collectionAtVisitRow.ts', import.meta.url).pathname,
})
const compiled = await bundle.generate({ format: 'esm' })
const { collectionAtVisitRow } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled.output[0].code).toString('base64')}`
)
const summaryBundle = await rolldown({
  input: new URL('../frontend/src/features/commercial-estimate/ArrivalAmountSummary.tsx', import.meta.url).pathname,
})
const summaryCompiled = await summaryBundle.generate({ format: 'esm' })
const { default: ArrivalAmountSummary } = await import(
  `data:text/javascript;base64,${Buffer.from(summaryCompiled.output[0].code).toString('base64')}`
)
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')

const result = {
  payer: 'Cigna',
  benefits: { active: true, network_status: 'in_network', network_source: 'payer_participation_message' },
  identity: { status: 'matched', match_version: 2, binding: 'synthetic-binding' },
  price_range: { mode: 'services', lines: [{ id: 'office', code: '99213', units: 1,
    benefits: { active: true, copay_cents: 6000, copay_period: 'VISIT',
      deductible_applies: false, coinsurance_percent: '0', oop_remaining_cents: 864094 } }] },
  temporary_rate_reference: { source: 'BCBS Texas clinic schedule', lines: [
    { id: 'office', code: '99213', units: 1, unit_reference_cents: 7615 },
  ] },
  fee_proxy_estimate: { basis: 'bcbstx_cigna_fee_proxy', patient_share_cents: 6000,
    message: 'BCBS Texas pricing is a reference only.' },
  collection_guidance: { amount_cents: 6000, status: 'estimated', basis: 'office_copay',
    reason: 'Collect the fixed office copay.' },
  encounter_summary: { collect_today_cents: 6000, payments_verified_cents: 0 },
}
const model = { cash: false, calculating: false, previousEstimate: false,
  supportedAmount: true, amount: '$60.00' }
const values = { combinedPlan: null, feeProxy: result.fee_proxy_estimate, aetnaPlanning: null,
  aetnaCopayPlanning: null, officeReferenceCopay: null, humanaReferenceCopay: null,
  partialOfficeCopay: null, pendingAetnaCopay: null, priorDateAetnaCopay: null,
  pendingBluecardCopay: null, cardPlanningCopay: null, cignaCard: null,
  automaticCashFallback: false, cignaFeeProxy: true, proxyFormula: { total: '$60.00' } }

const row = collectionAtVisitRow(result, model, values)
assert.equal(row.label, 'Amount to collect at visit')
assert.equal(row.value, '$60.00')
assert.match(row.detail, /Office copay advance/)
const summary = renderToStaticMarkup(React.createElement(ArrivalAmountSummary, {
  model: { ...model, amountText: '$60.00', amountLabel: 'Amount to collect now' },
  result, partialCopay: null, visibleAmount: true,
}))
assert.match(summary, /Amount to collect now/)
assert.match(summary, /\$60\.00/)
assert.doesNotMatch(summary, /Cigna planning estimate|collection pending/)

const pending = collectionAtVisitRow({ ...result,
  collection_guidance: { amount_cents: null, status: 'action_required', basis: 'missing_collection_inputs' },
}, { ...model, supportedAmount: false, amount: null }, values)
assert.match(pending.value, /planning estimate/)
console.log('Cigna office collection: approved $60 copay wins over BCBS fee proxy; pending cases stay pending')
