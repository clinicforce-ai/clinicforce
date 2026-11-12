import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { default: ArrivalAmountSummary } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/ArrivalAmountSummary.tsx',
  )
  const { default: CommercialCostBreakdown } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialCostBreakdown.tsx',
  )
  const { deriveCommercialEstimateView } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/deriveCommercialEstimateView.ts',
  )
  const result = {
    payer: 'Blue Cross Blue Shield Tx',
    service_date: '2026-11-12',
    identity: { status: 'matched', binding: 'synthetic-policy', match_version: 2 },
    plan_details: { stedi: { names: ['PREFERRED PROVIDER OPTION MEDICAL'] } },
    benefits: {
      active: true,
      service_date_matches: true,
      network_status: 'in_network',
      network_source: 'clinic_library_contract',
      reported_options: [
        { kind: 'deductible', service_type_code: '30', coverage_level: 'FAMILY',
          network: 'IN_NETWORK', plan_association: 'medical', plan_index: 0,
          time_period: 'REMAINING', amount_cents: 300000 },
        { kind: 'coinsurance', service_type_code: '98', coverage_level: 'INDIVIDUAL',
          network: 'IN_NETWORK', plan_association: 'medical', plan_index: 0,
          percent: '20', payer_note: 'BENEFITCLASS-SPECIALIST' },
        { kind: 'out_of_pocket', service_type_code: '30', coverage_level: 'FAMILY',
          network: 'IN_NETWORK', plan_association: 'medical', plan_index: 0,
          time_period: 'REMAINING', amount_cents: 500000 },
      ],
    },
    codes: [{ id: 'office', code: '99203', units: 1, modifiers: [] }],
    selected_ids: ['office'],
    price_range: { mode: 'services', complete: false, service_issues: [],
      patient_min_cents: null, patient_max_cents: null,
      lines: [{ id: 'office', code: '99203', units: 1, modifiers: [], allowed_cents: null,
        benefits: { active: true, network_status: 'in_network', copay_cents: null,
          coinsurance_percent: '20', deductible_applies: null } }] },
    temporary_rate_reference: { complete: true, source: 'NMCT Clinical Library Blue Cross fee schedule',
      lines: [{ id: 'office', code: '99203', units: 1, modifiers: [],
        unit_reference_cents: 12345, source_reference: 'library:synthetic-fee' }] },
    collection_guidance: { amount_cents: 12345, status: 'estimated',
      basis: 'bcbstx_refundable_advance', reference_total_cents: 12345,
      clinic_fee_reference_cents: 12345, services: [],
      reason: 'Collect the refundable clinic office quote; family deductible assumed.',
      collection_rule: { mode: 'blue_ppo_family_deductible_quote' } },
    encounter_summary: { collect_today_cents: 12345, payments_verified_cents: 0 },
  }
  const model = { cash: false, calculating: false, previousEstimate: false,
    supportedAmount: true, amount: '$123.45', amountText: '$123.45',
    networkLabel: 'In network', cashPrice: '$250.00',
    guidance: result.collection_guidance }
  const arrival = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
    { result, model, partialCopay: null, visibleAmount: true }))
  const table = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
    { result, model }))
  assert.match(arrival, /\$123\.45/)
  assert.match(table, /\$123\.45 provisional · family deductible assumed/)
  assert.match(table, /Amount to collect at visit/)
  assert.match(table, /Not established · \$123\.45 clinic fee reference/)
  const derived = deriveCommercialEstimateView({ result, appointments: [],
    selectedVisit: null, busy: null, error: null, readOnly: false, recovering: false })
  assert.equal(derived.supportedAmount, true)
  assert.equal(derived.amount, '$123.45')
  const testing = structuredClone(result)
  testing.codes = [{ id: 'ncs', code: '95908', units: 1, modifiers: [] },
    { id: 'emg', code: '95886', units: 2, modifiers: [] }]
  testing.selected_ids = ['ncs', 'emg']
  testing.price_range.lines = testing.codes.map((code) => ({ ...code, allowed_cents: null,
    benefits: { active: true, network_status: 'in_network', copay_cents: null,
      coinsurance_percent: '20', deductible_applies: null } }))
  testing.temporary_rate_reference.lines = [
    { id: 'ncs', code: '95908', units: 1, modifiers: [], unit_reference_cents: 10511,
      source_reference: 'library:synthetic-ncs' },
    { id: 'emg', code: '95886', units: 2, modifiers: [], unit_reference_cents: 8918,
      source_reference: 'library:synthetic-emg' },
  ]
  testing.collection_guidance.amount_cents = 28347
  testing.collection_guidance.reference_total_cents = 28347
  testing.collection_guidance.clinic_fee_reference_cents = 28347
  testing.encounter_summary.collect_today_cents = 28347
  testing.emg_option = { calculation_version: 2, requested: true, included: true,
    additional_min_cents: null, additional_max_cents: null,
    total_min_cents: null, total_max_cents: null }
  const testingModel = { ...model, amount: '$283.47', amountText: '$283.47',
    guidance: testing.collection_guidance }
  const testingTable = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
    { result: testing, model: testingModel }))
  const testingArrival = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
    { result: testing, model: testingModel, partialCopay: null, visibleAmount: true }))
  assert.match(testingTable, /\$283\.47 provisional · family deductible assumed/)
  assert.match(testingTable, /Amount to collect at visit/)
  assert.match(testingArrival, /\$283\.47/)
  assert.doesNotMatch(testingArrival, /Review estimate/)
  const testingView = deriveCommercialEstimateView({ result: testing, appointments: [],
    selectedVisit: null, busy: null, error: null, readOnly: true, recovering: false })
  assert.equal(testingView.amount, '$283.47')
  assert.match(testingView.emg.message, /included in the visit collection quote/)
} finally {
  await server.close()
}
