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
const { deriveCommercialEstimateView } = await load('../frontend/src/features/commercial-estimate/deriveCommercialEstimateView.ts')
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

const primary = structuredClone(result)
primary.payer = 'Medicare of Texas'
primary.stedi_payer = { id: 'JDNSN', name: 'National Centers for Medicare & Medicaid Services' }
primary.recovery = {}
primary.benefits.has_secondary = false
primary.benefits.medicare_part_b_secondary = null
primary.benefits.copay_cents = null
primary.benefits.original_medicare = true
primary.benefits.deductible_annual_cents = 28300
primary.benefits.deductible_remaining_cents = 0
primary.benefits.coinsurance_percent = '20'
primary.benefits.has_secondary = false
primary.price_range = { mode: 'services', complete: true,
  patient_min_cents: 2392, patient_max_cents: 2392,
  lines: [{ id: 'office', code: '99203', units: 1, allowed_cents: 11960,
    allowed_basis: 'medicare_fee_schedule', calculation: { patient_cents: 2392,
      plan_cents: 9568 }, benefits: { active: true, deductible_remaining_cents: 0,
      coinsurance_percent: '20' } }] }
primary.collection_guidance = { amount_cents: 2392, status: 'estimated', basis: 'anticipated_services',
  services: [],
  reason: 'Estimated 20% of the clinic-approved Medicare office fee after a met deductible.',
  collection_rule: { mode: 'original_medicare_office_coinsurance', reference: 'Clinic Medicare schedule' } }
primary.encounter_summary = { collect_today_cents: 2392, complete: true,
  estimated_total_patient_min_cents: 2392, estimated_total_patient_max_cents: 2392 }
const primaryModel = deriveCommercialEstimateView({ result: primary,
  appointments: [], selectedVisit: null, busy: false, error: null,
  readOnly: false, recovering: false })
assert.equal(primaryModel.supportedAmount, true)
assert.equal(primaryModel.amountText, '$23.92')
const primaryTable = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
  { model: primaryModel, result: primary }))
assert.match(primaryTable, /Amount to collect at visit/)
assert.match(primaryTable, /\$23\.92/)
assert.match(primaryTable, /20%/)
assert.match(primaryTable, /Provider billing status/)
assert.match(primaryTable, /Original Medicare has no plan network tier/)

const medicareWithSecondary = structuredClone(primary)
medicareWithSecondary.benefits.has_secondary = true
medicareWithSecondary.price_range.complete = false
medicareWithSecondary.price_range.lines[0].calculation = null
medicareWithSecondary.price_range.lines[0].benefits.has_secondary = true
medicareWithSecondary.price_range.lines[0].rate_resolution = { status: 'RESOLVED' }
medicareWithSecondary.recovery = { reason_code: 'multiple_active_policies', active_policy_count: 2 }
medicareWithSecondary.coverage_records = [
  { id: 'primary', status: 'active', order: 1, payer: ['Medicare of Texas'] },
  { id: 'secondary', status: 'active', order: 2, payer: ['Supplement'] },
]
medicareWithSecondary.insurance_details = { coverage_id: 'primary' }
medicareWithSecondary.collection_guidance = {
  amount_cents: 2392, primary_medicare_share_cents: 2392,
  status: 'estimated', basis: 'medicare_primary_coinsurance_advance',
  reason: '20% of the clinic Medicare fee before secondary payment.', services: [],
  collection_rule: { mode: 'original_medicare_office_coinsurance', reference: 'Clinic Medicare fee' },
}
medicareWithSecondary.encounter_summary = { collect_today_cents: 2392, complete: false }
const secondaryModel = deriveCommercialEstimateView({ result: medicareWithSecondary,
  appointments: [], selectedVisit: null, busy: false, error: null,
  readOnly: false, recovering: false })
assert.equal(secondaryModel.supportedAmount, true)
assert.equal(secondaryModel.amountText, '$23.92')
const secondaryTable = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
  { model: secondaryModel, result: medicareWithSecondary }))
assert.match(secondaryTable, /\$23\.92/)
assert.match(secondaryTable, /Medicare first/)
assert.match(secondaryTable, /provisional Medicare share before secondary payment/)
assert.match(secondaryTable, /No Original Medicare annual maximum/)

const pendingMedicare = structuredClone(medicareWithSecondary)
pendingMedicare.eligibility_request_state = 'pending'
pendingMedicare.collection_guidance.amount_cents = null
pendingMedicare.encounter_summary.collect_today_cents = null
const pendingModel = deriveCommercialEstimateView({ result: pendingMedicare,
  appointments: [], selectedVisit: null, busy: false, error: null,
  readOnly: false, recovering: false })
assert.equal(pendingModel.calculating, true)
assert.notEqual(pendingModel.amountText, '$0.00')

const texasPartB = structuredClone(primary)
texasPartB.stedi_payer.id = '04412'
texasPartB.benefits.original_medicare = true
texasPartB.benefits.deductible_annual_cents = 28300
texasPartB.benefits.deductible_remaining_cents = 0
texasPartB.price_range = { mode: 'services', complete: false, lines: [{
  id: 'office', code: '99203', units: 1, allowed_cents: null,
  benefits: { active: true, deductible_remaining_cents: 0, coinsurance_percent: '20' },
}] }
texasPartB.collection_guidance = { amount_cents: null, status: 'action_required',
  basis: 'missing_collection_inputs', services: [], reason: 'The Medicare fee is unavailable.' }
texasPartB.encounter_summary = { collect_today_cents: null, complete: false }
const texasModel = deriveCommercialEstimateView({ result: texasPartB,
  appointments: [], selectedVisit: null, busy: false, error: null,
  readOnly: false, recovering: false })
const texasTable = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
  { model: texasModel, result: texasPartB }))
assert.match(texasTable, /Provider billing status/)
assert.match(texasTable, /Original Medicare has no plan network tier/)
assert.match(texasTable, /Amount to collect at visit/)
assert.doesNotMatch(texasTable, /Amount to collect at visit[^]*?\$0\.00/)
assert.doesNotMatch(texasTable, /cash option|self-pay price/i)

const texasAlternatives = structuredClone(texasPartB)
texasAlternatives.service_date = '2026-10-13'
texasAlternatives.benefits.has_secondary = true
texasAlternatives.recovery = { reason_code: 'multiple_active_policies', active_policy_count: 2 }
texasAlternatives.price_range = { mode: 'alternatives', complete: false,
  lines: ['99202', '99203', '99204', '99205'].map(code => ({
    id: code, code, units: 1, allowed_cents: null, benefits: { active: true },
  })) }
texasAlternatives.encounter_summary = { collect_today_cents: 0, complete: false }
texasAlternatives.collection_guidance = { amount_cents: 0, status: 'zero_advance',
  basis: 'claim_after_service', services: [],
  reason: 'Multiple active policies need claim coordination.',
  collection_rule: { mode: 'original_medicare_office_deferral', reference: 'Clinic policy' } }
texasAlternatives.codes = []
const alternativesModel = deriveCommercialEstimateView({ result: texasAlternatives,
  appointments: [], selectedVisit: null, busy: false, error: null,
  readOnly: false, recovering: false })
assert.equal(alternativesModel.supportedAmount, true)
assert.equal(alternativesModel.amountText, '$0.00')
const alternativesTable = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
  { model: alternativesModel, result: texasAlternatives }))
assert.match(alternativesTable, /Amount to collect at visit/)
assert.match(alternativesTable, /\$0\.00/)
assert.doesNotMatch(alternativesTable, /cash option|self-pay price/i)
