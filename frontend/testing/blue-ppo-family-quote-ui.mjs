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
} finally {
  await server.close()
}
