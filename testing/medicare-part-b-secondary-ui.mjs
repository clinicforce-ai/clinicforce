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
primary.benefits.has_secondary = false
primary.benefits.medicare_part_b_secondary = null
primary.benefits.copay_cents = null
primary.benefits.deductible_annual_cents = 20000
primary.benefits.deductible_remaining_cents = 15000
primary.benefits.coinsurance_percent = '20'
primary.price_range = { mode: 'services', complete: true,
  patient_min_cents: 3800, patient_max_cents: 3800,
  lines: [{ id: 'office', code: '99203', units: 1, allowed_cents: 10000,
    allowed_basis: 'medicare_fee_schedule', calculation: { patient_cents: 3800,
      plan_cents: 6200 }, benefits: { active: true, deductible_remaining_cents: 15000,
      coinsurance_percent: '20' } }] }
primary.collection_guidance = { amount_cents: 0, status: 'zero_advance', basis: 'claim_after_service',
  reason: 'Clinic policy collects no advance for a verified Original Medicare office visit.',
  collection_rule: { mode: 'original_medicare_office_deferral', reference: 'Clinic policy' } }
primary.encounter_summary = { collect_today_cents: 0, complete: true,
  estimated_total_patient_min_cents: 3800, estimated_total_patient_max_cents: 3800 }
const primaryModel = { ...model, supportedAmount: true, amountText: '$0.00' }
const primaryTable = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
  { model: primaryModel, result: primary }))
assert.match(primaryTable, /Amount to collect at visit/)
assert.match(primaryTable, /\$0\.00/)
assert.match(primaryTable, /\$38\.00/)
assert.match(primaryTable, /20%/)
assert.match(primaryTable, /Provider billing status/)
assert.match(primaryTable, /Original Medicare has no plan network tier/)

const texasPartB = structuredClone(primary)
texasPartB.stedi_payer.id = '04412'
texasPartB.benefits.original_medicare = true
texasPartB.benefits.deductible_annual_cents = 28300
texasPartB.benefits.deductible_remaining_cents = 0
texasPartB.price_range = { mode: 'services', complete: false, lines: [{
  id: 'office', code: '99203', units: 1, allowed_cents: null,
  benefits: { active: true, deductible_remaining_cents: 0, coinsurance_percent: '20' },
}] }
texasPartB.encounter_summary = { collect_today_cents: 0, complete: false }
const texasTable = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
  { model: primaryModel, result: texasPartB }))
assert.match(texasTable, /Provider billing status/)
assert.match(texasTable, /Original Medicare has no plan network tier/)
assert.match(texasTable, /Amount to collect at visit/)
assert.match(texasTable, /\$0\.00/)
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
texasAlternatives.collection_guidance.services = []
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
