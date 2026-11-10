import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { rolldown } = require('rolldown')
async function load(path) {
  const bundle = await rolldown({ input: new URL(path, import.meta.url).pathname })
  const compiled = await bundle.generate({ format: 'esm' })
  return import(`data:text/javascript;base64,${Buffer.from(compiled.output[0].code).toString('base64')}`)
}
const { aetnaOfficePlanningCopay } = await load('../frontend/src/features/commercial-estimate/payerReportedPartialCopay.ts')
const { aetnaPriorDateReferenceCopay } = await load('../frontend/src/features/commercial-estimate/aetnaPriorDateReferenceCopay.ts')
const { collectionAtVisitRow } = await load('../frontend/src/features/commercial-estimate/collectionAtVisitRow.ts')
const { commercialOopRows } = await load('../frontend/src/features/commercial-estimate/commercialOopRows.ts')
const { suggestedDiagnosisRows } = await load('../frontend/src/features/commercial-estimate/suggestedDiagnosisRows.ts')
const { commercialRequirementRows } = await load('../frontend/src/features/commercial-estimate/commercialRequirementStatus.ts')

const terms = { active: true, network_status: 'in_network', service_date_matches: true,
  selected_benefit_ids: ['synthetic-office-copay'], copay_cents: 4000, coinsurance_percent: '0',
  deductible_applies: false, oop_remaining_cents: 0, separate_copay_limit: true,
  copay_remaining_cents: 90000 }
const result = { payer: 'Aetna', service_date: '2026-11-06',
  identity: { status: 'matched', binding: 'synthetic-current-binding', match_version: 2 },
  benefits: { active: true, network_status: 'in_network', network_source: 'payer_participation_message',
    service_date_matches: true, oop_remaining_cents: 0 },
  price_range: { complete: false, mode: 'services', lines: [
    { id: 'office', code: '99213', units: 1, benefits: terms },
  ] },
  collection_guidance: { amount_cents: null, basis: 'missing_collection_inputs',
    positive_advance_policy_missing: true, reason: 'The payer route needs an approved advance policy.' } }
const model = { cash: false, calculating: false, previousEstimate: false, supportedAmount: false }
const planning = aetnaOfficePlanningCopay(result, model, result.price_range.lines)
assert.equal(planning, 4000)
assert.equal(aetnaOfficePlanningCopay({ ...result, price_range: { ...result.price_range,
  lines: [{ ...result.price_range.lines[0], benefits: { ...terms, copay_remaining_cents: 1000 } }] } },
model, [{ ...result.price_range.lines[0], benefits: { ...terms, copay_remaining_cents: 1000 } }]), 1000)
assert.equal(aetnaOfficePlanningCopay({ ...result, price_range: { ...result.price_range,
  lines: [{ ...result.price_range.lines[0], benefits: { ...terms, copay_remaining_cents: null } }] } },
model, [{ ...result.price_range.lines[0], benefits: { ...terms, copay_remaining_cents: null } }]), null)
assert.equal(aetnaOfficePlanningCopay({ ...result, identity: { status: 'review' } },
model, result.price_range.lines), null)
assert.equal(aetnaOfficePlanningCopay(result, model, [
  { ...result.price_range.lines[0], benefits: { ...terms, separate_copay_limit: false } },
]), null)

const row = collectionAtVisitRow(result, model, { combinedPlan: null, feeProxy: null,
  aetnaPlanning: null, aetnaCopayPlanning: planning, officeReferenceCopay: null,
  humanaReferenceCopay: null, partialOfficeCopay: null, pendingAetnaCopay: null,
  cardPlanningCopay: null, automaticCashFallback: false, cignaFeeProxy: false,
  proxyFormula: undefined })
assert.match(row.label, /pending/i)
assert.match(row.value, /\$40\.00/)
assert.match(row.detail, /\$900\.00/)
assert.match(row.detail, /policy/i)
const paidRow = collectionAtVisitRow({ ...result, encounter_summary: { payments_verified_cents: 1000 } },
  model, { combinedPlan: null, feeProxy: null, aetnaPlanning: null, aetnaCopayPlanning: planning,
    officeReferenceCopay: null, humanaReferenceCopay: null, partialOfficeCopay: null,
    pendingAetnaCopay: null, cardPlanningCopay: null, automaticCashFallback: false,
    cignaFeeProxy: false, proxyFormula: undefined })
assert.match(paidRow.value, /\$30\.00/)

const oopRows = commercialOopRows(result, result, result.benefits, false, false, null)
assert.match(oopRows.find((entry) => entry.label === 'Individual out-of-pocket maximum').detail, /separate copay/i)
assert.match(oopRows.find((entry) => entry.label === 'Separate office copay maximum').value, /\$900\.00/)
const secondaryResult = {
  ...result,
  benefits: { ...result.benefits, has_secondary: true, primary_secondary_order: {
    status: 'verified', primary_payer_id: '60054', secondary_payer: 'Medicare',
    evidence_paths: ['synthetic-secondary-row'],
  } },
  recovery: { reason_code: 'multiple_active_policies', active_policy_count: 2 },
  price_range: { ...result.price_range, lines: [{ ...result.price_range.lines[0],
    benefits: { ...terms, oop_remaining_cents: 80000, separate_copay_limit: false,
      copay_remaining_cents: null } }] },
}
const secondaryCopay = aetnaOfficePlanningCopay(
  secondaryResult, model, secondaryResult.price_range.lines,
)
assert.equal(secondaryCopay, 4000)
const secondaryRow = collectionAtVisitRow(secondaryResult, model, {
  combinedPlan: null, feeProxy: null, aetnaPlanning: null,
  aetnaCopayPlanning: secondaryCopay, officeReferenceCopay: null,
  humanaReferenceCopay: null, partialOfficeCopay: null, pendingAetnaCopay: null,
  cardPlanningCopay: null, automaticCashFallback: false, cignaFeeProxy: false,
  proxyFormula: undefined,
})
assert.match(secondaryRow.label, /pending secondary claim/i)
assert.match(secondaryRow.value, /\$40\.00.*copay/i)
assert.match(secondaryRow.detail, /Medicare.*secondary|secondary.*Medicare/i)
assert.equal(aetnaOfficePlanningCopay({ ...secondaryResult, identity: { status: 'review' } },
  model, secondaryResult.price_range.lines), null)
assert.equal(aetnaOfficePlanningCopay({ ...secondaryResult, benefits: {
  ...secondaryResult.benefits, network_status: 'unknown' } },
model, secondaryResult.price_range.lines), null)
assert.match(suggestedDiagnosisRows('Dizziness, prior workup')[0].value, /R42/)
const priorDay = {
  ...secondaryResult,
  service_date: '2026-11-10',
  plan_details: { stedi: { names: ['Aetna Choice POS II'] } },
  requested_service_types: ['98'],
  benefits: { ...secondaryResult.benefits, service_date_matches: false,
    reported_options: [{ kind: 'copay', service_type_code: '98', coverage_level: 'INDIVIDUAL',
      network: 'IN_NETWORK', payer_note: 'Specialist Visit or Evaluation',
      plan_association: 'medical', amount_cents: 4000 }] },
  price_range: { ...secondaryResult.price_range, lines: [{ ...secondaryResult.price_range.lines[0],
    code: '99203', benefits: { ...secondaryResult.price_range.lines[0].benefits,
      service_date_matches: false } }] },
}
assert.equal(aetnaPriorDateReferenceCopay(priorDay, model), 4000)
const priorDayRow = collectionAtVisitRow(priorDay, model, {
  combinedPlan: null, feeProxy: null, aetnaPlanning: null,
  aetnaCopayPlanning: null, officeReferenceCopay: null, priorDateAetnaCopay: 4000,
  humanaReferenceCopay: null, partialOfficeCopay: null, pendingAetnaCopay: null,
  cardPlanningCopay: null, automaticCashFallback: false, cignaFeeProxy: false,
  proxyFormula: undefined,
})
assert.match(priorDayRow.value, /\$40\.00 Aetna office copay reference/i)
assert.match(priorDayRow.detail, /Medicare.*remittance/i)
assert.equal(aetnaPriorDateReferenceCopay({ ...priorDay, identity: { status: 'review' } }, model), null)
assert.equal(aetnaPriorDateReferenceCopay({ ...priorDay, benefits: {
  ...priorDay.benefits, network_status: 'unknown' } }, model), null)
const requirementRows = commercialRequirementRows(priorDay, false, false)
assert.match(requirementRows[0].value, /generally needs no referral/i)
assert.match(requirementRows[1].value, /absent from Aetna precert list/i)
const unknownNetworkPriorDay = {
  ...priorDay,
  benefit_source: 'Stedi',
  benefits: { ...priorDay.benefits, has_secondary: false, primary_secondary_order: undefined,
    network_status: 'unknown', network_source: undefined,
    reported_options: [{ ...priorDay.benefits.reported_options[0],
      coverage_level: 'EMPLOYEE_ONLY', amount_cents: 5500 }] },
  recovery: undefined,
  price_range: { ...priorDay.price_range, lines: [{ ...priorDay.price_range.lines[0],
    benefits: { ...priorDay.price_range.lines[0].benefits,
      copay_cents: 5500, copay_id: 'synthetic-stedi-office-copay',
      selected_benefit_ids: ['synthetic-stedi-office-copay'], network_status: 'unknown' } }] },
}
const unknownNetworkReference = aetnaPriorDateReferenceCopay(unknownNetworkPriorDay, model)
assert.equal(aetnaPriorDateReferenceCopay({ ...unknownNetworkPriorDay, benefits: {
  ...unknownNetworkPriorDay.benefits, reported_options: [{
    ...priorDay.benefits.reported_options[0], amount_cents: 6500 }] } }, model), null)
assert.equal(aetnaPriorDateReferenceCopay({ ...unknownNetworkPriorDay, benefits: {
  ...unknownNetworkPriorDay.benefits, reported_options: [{
    ...priorDay.benefits.reported_options[0], amount_cents: 5500 }] } }, model), 5500)
assert.equal(unknownNetworkReference, 5500)
assert.equal(aetnaPriorDateReferenceCopay({ ...unknownNetworkPriorDay,
  identity: { status: 'review' } }, model), null)
assert.equal(aetnaPriorDateReferenceCopay({ ...unknownNetworkPriorDay,
  benefit_source: 'eCW saved eligibility' }, model), null)
assert.equal(aetnaPriorDateReferenceCopay({ ...unknownNetworkPriorDay,
  price_range: { ...unknownNetworkPriorDay.price_range, lines: [{
    ...unknownNetworkPriorDay.price_range.lines[0], benefits: {
      ...unknownNetworkPriorDay.price_range.lines[0].benefits, copay_id: null,
    } }] } }, model), null)
const unknownNetworkRow = collectionAtVisitRow(unknownNetworkPriorDay, model, {
  combinedPlan: null, feeProxy: null, aetnaPlanning: null, aetnaCopayPlanning: null,
  officeReferenceCopay: null, priorDateAetnaCopay: unknownNetworkReference,
  humanaReferenceCopay: null, partialOfficeCopay: null, pendingAetnaCopay: null,
  cardPlanningCopay: null, automaticCashFallback: false, cignaFeeProxy: false,
  proxyFormula: undefined,
})
assert.match(unknownNetworkRow.value, /Pending.*\$55\.00.*Aetna office copay reference/i)
assert.match(unknownNetworkRow.label, /pending verification/i)
assert.match(unknownNetworkRow.detail, /saved response covers another date/i)
console.log('Aetna separate copay: $40 pending collection; $0 general OOP does not erase the separate cap')
