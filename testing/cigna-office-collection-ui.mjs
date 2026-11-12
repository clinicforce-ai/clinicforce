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
const viewBundle = await rolldown({
  input: new URL('../frontend/src/features/commercial-estimate/deriveCommercialEstimateView.ts', import.meta.url).pathname,
})
const viewCompiled = await viewBundle.generate({ format: 'esm' })
let deriveCommercialEstimateView
try {
  ;({ deriveCommercialEstimateView } = await import(
    `data:text/javascript;base64,${Buffer.from(viewCompiled.output[0].code).toString('base64')}`
  ))
} catch (error) {
  throw new Error(`Could not load derived estimate view: ${error.message}`)
}
const summaryBundle = await rolldown({
  input: new URL('../frontend/src/features/commercial-estimate/ArrivalAmountSummary.tsx', import.meta.url).pathname,
})
const summaryCompiled = await summaryBundle.generate({ format: 'esm' })
const { default: ArrivalAmountSummary } = await import(
  `data:text/javascript;base64,${Buffer.from(summaryCompiled.output[0].code).toString('base64')}`
)
const costStateBundle = await rolldown({
  input: new URL('../frontend/src/features/commercial-estimate/commercialCostState.ts', import.meta.url).pathname,
})
const costStateCompiled = await costStateBundle.generate({ format: 'esm' })
const { applyBluePpoFamilyQuote } = await import(
  `data:text/javascript;base64,${Buffer.from(costStateCompiled.output[0].code).toString('base64')}`
)
const cptBundle = await rolldown({
  input: new URL('../frontend/src/features/commercial-estimate/commercialCptRows.ts', import.meta.url).pathname,
})
const cptCompiled = await cptBundle.generate({ format: 'esm' })
const { commercialAllowedTotalRow } = await import(
  `data:text/javascript;base64,${Buffer.from(cptCompiled.output[0].code).toString('base64')}`
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
const deposit = { ...result, fee_proxy_estimate: null,
  benefits: { active: true, network_status: 'unknown' },
  price_range: { mode: 'services', lines: [{ id: 'office', code: '99213', units: 1,
    benefits: { active: true } }] },
  collection_guidance: { amount_cents: 7615, status: 'estimated',
    basis: 'bcbstx_refundable_advance', reference_total_cents: 7615,
    reason: 'Refundable advance; final liability unknown.', services: [] },
  encounter_summary: { collect_today_cents: 7615, estimated_total_patient_min_cents: null },
}
const depositModel = { ...model, amount: '$76.15', amountText: '$76.15',
  amountLabel: 'Refundable advance to collect now' }
const derivedDeposit = deriveCommercialEstimateView({
  result: deposit, appointments: [], selectedVisit: null, busy: false,
  error: null, readOnly: false, recovering: false,
})
assert.equal(derivedDeposit.supportedAmount, true)
assert.equal(derivedDeposit.amountLabel, 'Refundable advance to collect now')
const depositRow = collectionAtVisitRow(deposit, depositModel,
  { ...values, feeProxy: null, cignaFeeProxy: false })
assert.equal(depositRow.label, 'Refundable advance at visit')
assert.equal(depositRow.value, '$76.15')
assert.match(depositRow.detail, /refund any excess/)
const depositSummary = renderToStaticMarkup(React.createElement(ArrivalAmountSummary, {
  model: depositModel, result: deposit, partialCopay: null, visibleAmount: true,
}))
assert.match(depositSummary, /Refundable advance to collect now/)
assert.match(depositSummary, /\$76\.15/)
const ppoQuote = { ...deposit,
  plan_details: { stedi: { names: ['PPO'] } },
  collection_guidance: { ...deposit.collection_guidance,
    reason: 'Collect the refundable Cigna PPO office quote; reconcile after the claim.',
    collection_rule: { mode: 'cigna_ppo_deductible_quote' },
    clinic_fee_reference_cents: 7615 },
}
const derivedPpoQuote = deriveCommercialEstimateView({
  result: ppoQuote, appointments: [], selectedVisit: null, busy: false,
  error: null, readOnly: false, recovering: false,
})
assert.equal(derivedPpoQuote.supportedAmount, true)
const ppoQuoteRow = collectionAtVisitRow(ppoQuote, depositModel,
  { ...values, feeProxy: null, cignaFeeProxy: false })
assert.equal(ppoQuoteRow.label, 'Amount to collect at visit')
assert.equal(ppoQuoteRow.value, '$76.15')
assert.match(ppoQuoteRow.detail, /refundable Cigna PPO office quote/)
const ppoShareRows = [{ label: 'Estimated patient share', value: 'Not fully calculated' }]
applyBluePpoFamilyQuote(ppoShareRows, ppoQuote)
assert.equal(ppoShareRows[0].value, '$76.15 provisional · office deductible assumed')
const ppoAllowedRow = commercialAllowedTotalRow(ppoQuote, {
  healthselectZero: false, cardPlanningCopay: null, dsnpOffice: false,
  automaticCashFallback: false, lineAllowedTotal: null,
  bluecardDeductible: null, botoxPreview: null,
})
assert.match(ppoAllowedRow.value, /Not established.*\$76\.15 clinic fee reference/)
const unconfirmedCopay = { ...deposit,
  price_range: { mode: 'services', lines: [{ id: 'office', code: '99205', units: 1,
    benefits: { active: true, copay_cents: 10000 } }] },
  collection_guidance: { amount_cents: 10000, status: 'estimated',
    basis: 'cigna_refundable_office_copay', payer_reported_office_copay_cents: 10000,
    reason: 'Refundable payer copay; provider tier remains unconfirmed.', services: [] },
  encounter_summary: { collect_today_cents: 10000, estimated_total_patient_min_cents: null },
}
const derivedUnconfirmedCopay = deriveCommercialEstimateView({
  result: unconfirmedCopay, appointments: [], selectedVisit: null, busy: false,
  error: null, readOnly: false, recovering: false,
})
assert.equal(derivedUnconfirmedCopay.supportedAmount, true)
assert.equal(derivedUnconfirmedCopay.amountLabel, 'Refundable advance to collect now')
const unconfirmedRow = collectionAtVisitRow(unconfirmedCopay,
  { ...depositModel, amount: '$100.00', amountText: '$100.00' },
  { ...values, feeProxy: null, cignaFeeProxy: false })
assert.equal(unconfirmedRow.value, '$100.00')
assert.match(unconfirmedRow.detail, /Provider participation.*unconfirmed/)
console.log('Cigna office collection: approved $60 copay wins over BCBS fee proxy; pending cases stay pending')
