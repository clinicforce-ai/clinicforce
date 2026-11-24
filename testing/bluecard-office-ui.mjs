import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { rolldown } = require('rolldown')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
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
assert.equal(pendingBluecardOfficeCopay({ ...result, benefits: { ...result.benefits, network_source: null,
  network_status: 'unknown' } }, model), 6000)
assert.equal(pendingBluecardOfficeCopay(result, { ...model, requirementIssue: 'Check authorization' }), 6000)
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
const feeSchedulePrompt = 'Please enter rate for CPT 95908 from the United Healthcare fee schedule.'
const missingBothRow = collectionAtVisitRow({ ...unmatchedPlan,
  payer: 'United Healthcare',
  price_range: { mode: 'services', lines: [{ id: 'ncs', code: '95908', units: 1,
    allowed_cents: null, issues: [feeSchedulePrompt],
    rate_resolution: { status: 'RATE_UNAVAILABLE' }, benefits: terms }] },
  payment_explanation: { reason_code: 'contract_rate_missing', sentence: 'Rate unavailable.' },
}, model, { ...values, pendingBluecardCopay: null })
assert.match(missingBothRow.value, /Please enter rate for CPT 95908 from the United Healthcare fee schedule/)
const plusDeferral = collectionAtVisitRow({
  ...result,
  price_range: { mode: 'services', lines: [{ id: 'office', code: '99213', units: 1,
    allowed_cents: 7615, benefits: { active: true, network_status: 'in_network',
      coinsurance_percent: '20.0' } }] },
  collection_guidance: { amount_cents: 0, basis: 'claim_after_service',
    collection_rule: { mode: 'bcbstx_plus_office_deferral' },
    reason: 'Collect $0 at check-in. $15.23 and $76.15 are conditional examples.' },
}, { ...model, supportedAmount: true }, { ...values, pendingBluecardCopay: null })
assert.equal(plusDeferral.value, '$0.00')
assert.match(plusDeferral.detail, /\$15\.23 and \$76\.15 are conditional examples/)
async function load(path) {
  const built = await rolldown({ input: new URL(path, import.meta.url).pathname,
    plugins: [{ name: 'ignore-css', load(id) {
      if (id.endsWith('.css')) return { code: 'export default {}', moduleType: 'js' }
    } }],
  })
  const output = await built.generate({ format: 'esm' })
  return import(`data:text/javascript;base64,${Buffer.from(output.output[0].code).toString('base64')}`)
}
const { deriveCommercialEstimateView } = await load('../frontend/src/features/commercial-estimate/deriveCommercialEstimateView.ts')
const CommercialCostBreakdown = (await load('../frontend/src/features/commercial-estimate/CommercialCostBreakdown.tsx')).default
const ArrivalAmountSummary = (await load('../frontend/src/features/commercial-estimate/ArrivalAmountSummary.tsx')).default
const plusVisit = {
  payer: 'Blue Cross Blue Shield Tx', service_date: '2026-11-19',
  identity: { status: 'matched', binding: 'synthetic-plus-member', match_version: 2 },
  selected_ids: ['office'], codes: [{ id: 'office', code: '99213', units: 1, modifiers: [] }],
  plan_details: { stedi: { names: ['PREFERRED PROVIDER OPTION PLUS MEDICAL'] } },
  benefits: { active: true, service_date_matches: true, network_status: 'in_network',
    network_source: 'clinic_group_agreement', deductible_annual_cents: 400000,
    oop_annual_cents: 750000, coinsurance_percent: '20.0' },
  price_range: { mode: 'services', complete: false, lines: [{ id: 'office', code: '99213',
    units: 1, modifiers: [], allowed_cents: 7615, allowed_basis: 'clinic_contract',
    benefits: { active: true, network_status: 'in_network', coinsurance_percent: '20.0' } }] },
  collection_guidance: { amount_cents: 0, status: 'zero_advance', basis: 'claim_after_service',
    reason: 'Collect $0 at check-in. If the deductible is met, 20% is $15.23; if the full allowance applies to an unmet deductible, it is $76.15. These are conditional examples.',
    collection_rule: { mode: 'bcbstx_plus_office_deferral' }, services: [] },
  encounter_summary: { collect_today_cents: 0, complete: false, payments_verified_cents: 0 },
}
const plusModel = deriveCommercialEstimateView({ result: plusVisit, appointments: [],
  selectedVisit: '', busy: null, error: '', readOnly: true, recovering: false })
assert.equal(plusModel.supportedAmount, true)
assert.equal(plusModel.amount, '$0.00')
const plusTable = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
  { result: plusVisit, model: plusModel }))
assert.match(plusTable, /Estimated patient share[\s\S]*Not fully calculated/)
assert.match(plusTable, /Amount to collect at visit[\s\S]*\$0\.00/)
assert.match(plusTable, /\$15\.23/)
assert.match(plusTable, /\$76\.15/)
const plusArrival = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
  { result: plusVisit, model: plusModel, partialCopay: null, visibleAmount: true }))
assert.match(plusArrival, /Amount to collect at visit[\s\S]*\$0\.00/)
const newOfficeVisit = {
  ...plusVisit,
  service_date: '2026-11-20',
  codes: [{ id: 'office', code: '99204', units: 1, modifiers: [] }],
  benefits: { ...plusVisit.benefits, deductible_annual_cents: null,
    oop_annual_cents: null, coinsurance_percent: null },
  price_range: { ...plusVisit.price_range, lines: [{
    ...plusVisit.price_range.lines[0], code: '99204', allowed_cents: 16000,
    benefits: { active: true, network_status: 'in_network', coinsurance_percent: null },
  }] },
  collection_guidance: { ...plusVisit.collection_guidance,
    reason: 'Collect $0 at check-in; applicable cost-sharing terms were not returned for this CPT.' },
}
const newOfficeModel = deriveCommercialEstimateView({ result: newOfficeVisit, appointments: [],
  selectedVisit: '', busy: null, error: '', readOnly: true, recovering: false })
const newOfficeTable = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
  { result: newOfficeVisit, model: newOfficeModel }))
assert.match(newOfficeTable, /Estimated patient share[\s\S]*Not fully calculated/)
assert.match(newOfficeTable, /Amount to collect at visit[\s\S]*\$0\.00/)
assert.match(newOfficeTable, /cost-sharing terms were not returned/)
const newOfficeArrival = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
  { result: newOfficeVisit, model: newOfficeModel, partialCopay: null, visibleAmount: true }))
assert.match(newOfficeArrival, /Amount to collect at visit[\s\S]*\$0\.00/)
console.log('Blue office tables: $60 pending and Plus Medical $0 deferred for established and new office visits')
