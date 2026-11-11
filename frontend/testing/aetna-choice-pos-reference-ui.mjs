import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { default: CommercialCostBreakdown } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialCostBreakdown.tsx',
  )
  const { default: TemporaryRateReference } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/TemporaryRateReference.tsx',
  )
  const result = {
    payer: 'Aetna',
    service_date: '2026-11-11',
    reason: 'Synthetic EMG/NCS evaluation',
    identity: { status: 'matched', binding: 'synthetic-binding', match_version: 2 },
    plan_details: { stedi: { names: ['Aetna Choice POS II'] } },
    benefits: {
      active: true,
      service_date_matches: true,
      network_status: 'unknown',
      family_oop_annual_cents: 100000,
      family_oop_remaining_cents: 0,
      reported_options: [
        { kind: 'copay', network: 'IN_NETWORK', service_type_code: '73', amount_cents: 0 },
        { kind: 'copay', network: 'IN_NETWORK', service_type_code: '73', amount_cents: 1000 },
        { kind: 'copay', network: 'IN_NETWORK', service_type_code: '73', amount_cents: 1500 },
        { kind: 'coinsurance', network: 'IN_NETWORK', service_type_code: '73', percent: '0' },
      ],
    },
    recovery: { reason_code: 'multiple_active_policies', active_policy_count: 2 },
    price_range: {
      mode: 'services',
      complete: false,
      lines: [
        { id: 'ncs', code: '95908', units: 1, modifiers: [], allowed_cents: null },
        { id: 'emg', code: '95886', units: 1, modifiers: [], allowed_cents: null },
      ],
    },
    temporary_rate_reference: {
      source: 'NMCT Clinical Library Blue Cross fee schedule',
      complete: true,
      message: 'Blue Cross rates, not Aetna allowances or a collection amount.',
      lines: [
        { id: 'ncs', code: '95908', units: 1, modifiers: [], unit_reference_cents: 10511 },
        { id: 'emg', code: '95886', units: 1, modifiers: [], unit_reference_cents: 8918 },
      ],
    },
    collection_guidance: { amount_cents: null, basis: 'missing_collection_inputs' },
  }
  const model = {
    cash: false,
    planName: 'Aetna Choice POS II',
    networkLabel: 'Not determined',
    policyStatus: 'Active',
    supportedAmount: false,
    amountText: 'Insured amount unavailable',
  }
  const table = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, { model, result }),
  )
  for (const value of [
    '$105.11 BCBS Texas reference',
    '$89.18 BCBS Texas reference',
    '$194.29 total reference',
    'not Aetna allowances or a collection amount',
    'CPT 95908: No; CPT 95886: No',
  ])
    assert(table.includes(value), `Missing Choice POS II reference ${value}`)
  assert(!table.includes('Amount to collect at visit</th><td><span>$194.29'))
  const detail = renderToStaticMarkup(React.createElement(TemporaryRateReference, { result }))
  assert(detail.includes('CPT 95908') && detail.includes('CPT 95886'))
  result.collection_guidance = {
    amount_cents: 0,
    basis: 'claim_after_service',
    reason:
      'Collect $0 at check-in. min(any applicable copay + $0 deductible + 0% × Aetna allowed amount, $0 family in-network remaining) = $0.00 for covered in-network care.',
    collection_rule: {
      mode: 'aetna_choice_family_oop_deferral',
      reference: 'Synthetic payer response',
    },
  }
  const zeroTable = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, { model, result }),
  )
  assert(zeroTable.includes('Amount to collect at visit'))
  assert(zeroTable.includes('$0.00'))
  assert(zeroTable.includes('$0.00 if covered in-network'))
  assert(zeroTable.includes('$0.00 / $10.00 / $15.00 returned by diagnostic category'))
  assert(zeroTable.includes('0% across returned in-network diagnostic categories'))
  assert(zeroTable.includes('min(any applicable copay + $0 deductible + 0% × Aetna allowed amount'))
  const { default: ArrivalAmountSummary } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/ArrivalAmountSummary.tsx',
  )
  const summary = renderToStaticMarkup(
    React.createElement(ArrivalAmountSummary, {
      model,
      result,
      partialCopay: null,
      visibleAmount: true,
    }),
  )
  assert(summary.includes('Amount to collect at visit'))
  assert(summary.includes('$0.00'))
} finally {
  await server.close()
}
