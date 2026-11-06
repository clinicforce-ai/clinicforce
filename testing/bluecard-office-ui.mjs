import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { rolldown } = require('rolldown')
const bundle = await rolldown({
  input: new URL('../frontend/src/features/commercial-estimate/collectionAtVisitRow.ts', import.meta.url).pathname,
})
const compiled = await bundle.generate({ format: 'esm' })
const { pendingBluecardOfficeCopay, collectionAtVisitRow } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled.output[0].code).toString('base64')}`
)
const benefitBundle = await rolldown({
  input: new URL('../frontend/src/features/commercial-estimate/payerReportedPartialCopay.ts', import.meta.url).pathname,
})
const benefitModule = await benefitBundle.generate({ format: 'esm' })
const { specialistOfficeCopay } = await import(
  `data:text/javascript;base64,${Buffer.from(benefitModule.output[0].code).toString('base64')}`
)

const officeBenefit = { benefits: { active: true, reported_options: [{
  kind: 'copay', service_type_code: '98', coverage_level: 'INDIVIDUAL', network: 'IN_NETWORK',
  places_of_service: [], payer_note: 'SPECIALIST', amount_cents: 2500, time_period: 'VISIT',
}] }, network_evidence: { place_of_service: '11' } }
assert.equal(specialistOfficeCopay(officeBenefit), 2500)
assert.equal(specialistOfficeCopay({ ...officeBenefit, network_evidence: undefined, requested_service_types: ['98'] }), 2500)
assert.equal(specialistOfficeCopay({ ...officeBenefit, network_evidence: { place_of_service: '02' } }), null)
assert.equal(specialistOfficeCopay({ ...officeBenefit, network_evidence: { place_of_service: '02' }, requested_service_types: ['98'] }), null)
assert.equal(specialistOfficeCopay({ ...officeBenefit, telehealth: true }), null)

const identity = { status: 'warning', match_version: 2, binding: 'synthetic-binding',
  reconciliation_rule: 'payer_matched_patient' }
const terms = { active: true, network_status: 'in_network', copay_cents: 6000,
  copay_period: '7', deductible_applies: false, oop_remaining_cents: 443428, identity }
const result = {
  payer: 'BCBSTX', identity,
  benefits: { active: true, network_status: 'in_network', network_source: 'clinic_library_contract' },
  plan_details: { stedi: { names: ['Example PPO ALTNET'], verification_payer: {
    name: 'Example Blue home plan', payer_id: '123', evidence_path: 'plans[0]' } } },
  price_range: { mode: 'services', lines: [{ id: 'office', code: '99204', units: 1, benefits: terms }] },
  collection_guidance: { amount_cents: null, basis: 'missing_collection_inputs' },
}
const model = { cash: false, calculating: false, previousEstimate: false,
  amount: null, supportedAmount: false }
const values = { combinedPlan: null, feeProxy: null, aetnaPlanning: null,
  aetnaCopayPlanning: null, officeReferenceCopay: null, humanaReferenceCopay: null,
  partialOfficeCopay: null, pendingAetnaCopay: null, cardPlanningCopay: null,
  automaticCashFallback: false, cignaFeeProxy: false, proxyFormula: undefined }
const copay = pendingBluecardOfficeCopay(result, model)
assert.equal(copay, 6000)
const row = collectionAtVisitRow(result, model, { ...values, pendingBluecardCopay: copay })
assert.match(row.label, /pending verification/)
assert.match(row.value, /\$60\.00/)
assert.match(row.detail, /home-plan authorization/)
assert.equal(pendingBluecardOfficeCopay({ ...result, identity: { ...identity, status: 'review' } }, model), null)
assert.equal(pendingBluecardOfficeCopay({ ...result, benefits: { ...result.benefits, has_secondary: true } }, model), null)
assert.equal(pendingBluecardOfficeCopay({ ...result, benefits: { ...result.benefits, network_source: null } }, model), null)
assert.equal(pendingBluecardOfficeCopay(result, { ...model, requirementIssue: 'Check authorization' }), null)
assert.equal(pendingBluecardOfficeCopay({ ...result, price_range: { mode: 'services', lines: [
  { id: 'office', code: '99204', units: 1, benefits: { ...terms, network_status: 'out_of_network' } },
] } }, model), null)
assert.equal(pendingBluecardOfficeCopay({ ...result, price_range: { mode: 'services', lines: [
  { id: 'office', code: '99204', units: 1, benefits: terms },
  { id: 'test', code: '95910', units: 1, benefits: terms },
] } }, model), null)
assert.equal(pendingBluecardOfficeCopay({ ...result, encounter_summary: { payments_verified_cents: 2000 } }, model), 4000)
const unmatchedPlan = {
  ...result,
  price_range: { mode: 'services', lines: [{ id: 'office', code: '99203', units: 1,
    allowed_cents: null, rate_resolution: { status: 'RATE_UNAVAILABLE' },
    benefits: { active: true, network_status: 'in_network',
      deductible_annual_cents: 400000, deductible_remaining_cents: 400000,
      coinsurance_percent: '20', copay_cents: null } }] },
  payment_explanation: { reason_code: 'clinic_product_mismatch',
    sentence: 'Coverage is active. Link the member plan to the clinic agreement and rerun the estimate.' },
}
const unmatchedRow = collectionAtVisitRow(unmatchedPlan, model, { ...values, pendingBluecardCopay: null })
assert.match(unmatchedRow.value, /match.*plan.*clinic/i)
assert.match(unmatchedRow.detail, /link the member plan/i)
assert.doesNotMatch(unmatchedRow.value, /not established|cannot be established/i)
const missingRateRow = collectionAtVisitRow({ ...unmatchedPlan,
  payment_explanation: { reason_code: 'contract_rate_missing',
    sentence: 'Coverage is active. Verify the clinic rate for CPT 99203.' },
}, model, { ...values, pendingBluecardCopay: null })
assert.match(missingRateRow.value, /check plan-to-rate match/i)
assert.match(missingRateRow.detail, /CPT 99203/)
console.log('BlueCard office table: $60 pending; identity, payer-order, network, service, and payment guards passed')
