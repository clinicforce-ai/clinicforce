import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { default: CommercialCostBreakdown } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialCostBreakdown.tsx',
  )
  const { default: ArrivalAmountSummary } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/ArrivalAmountSummary.tsx',
  )
  const model = { cash: false, supportedAmount: false, amountText: 'Insured amount unavailable',
    planName: 'Optum Care', networkLabel: 'Not determined', policyStatus: 'Unknown' }
  const result = {
    payer: 'Optum Care', payment_mode: 'insurance', service_date: '2026-11-20',
    reason: 'Three-month follow-up',
    network_evidence: { place_of_service: '11' },
    card_check: { status: 'conflict', conflicts: ['member_id'], differences: ['payer_name'],
      published_oon_specialist_copay_cents: 8500,
      published_oon_source_url: 'https://www.uhc.com/medicare/alphadog/AANM26LP0332760_000' },
    benefits: { active: false, network_status: 'unknown' },
    recovery: { state: 'complete', outcome: 'no_match', reason_code: 'multiple_active_policies' },
    collection_guidance: { amount_cents: null, status: 'action_required', basis: 'missing_collection_inputs' },
    price_range: { mode: 'services', complete: false, lines: [
      { id: 'office', code: '99213', units: 1, modifiers: [], allowed_cents: null,
        benefits: { active: false }, rate_resolution: { status: 'UNRESOLVED' } },
    ] },
  }
  const table = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
    { model, result }))
  assert(table.includes('Amount to collect at visit · verification pending'))
  assert(table.includes('$85.00 conditional plan reference · do not collect yet'))
  assert(table.includes('$85.00 published out-of-network specialist copay'))
  assert(!table.includes('$150.00</span><small>'))
  const arrival = renderToStaticMarkup(React.createElement(ArrivalAmountSummary, {
    model, result, partialCopay: null, visibleAmount: true,
  }))
  assert(arrival.includes('$85.00 conditional'))
  assert(arrival.includes('not an authorized charge'))
  for (const changed of [
    { billing_protection: { status: 'qmb' } },
    { network_evidence: { place_of_service: '02' } },
    { benefits: { ...result.benefits, qmb_status: 'protected' } },
    { price_range: { ...result.price_range, lines: [...result.price_range.lines,
      { id: 'test', code: '95908', units: 1 }] } },
    { card_check: { ...result.card_check, published_oon_specialist_copay_cents: undefined } },
  ]) {
    const blocked = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
      { model, result: { ...result, ...changed } }))
    assert(!blocked.includes('$85.00 conditional plan reference'))
  }
  const cashFallbackResult = { ...result, cash_fallback: { reason: 'Search incomplete' },
    collection_guidance: { amount_cents: 15000, status: 'estimate', basis: 'practice_cash_price' } }
  const automaticCash = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
    { model: { ...model, cash: true }, result: cashFallbackResult }))
  assert(automaticCash.includes('$85.00 conditional plan reference'))
  const automaticCashArrival = renderToStaticMarkup(React.createElement(ArrivalAmountSummary, {
    model: { ...model, cash: true }, result: cashFallbackResult,
    partialCopay: null, visibleAmount: true,
  }))
  assert(automaticCashArrival.includes('$85.00 conditional'))
  assert(!automaticCashArrival.includes('$150.00'))
  const explicitCash = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
    { model: { ...model, cash: true }, result }))
  assert(!explicitCash.includes('$85.00 conditional plan reference'))
  console.log('UHC NM-0006 reference: $85 conditional, QMB and service guards passed')
} finally {
  await server.close()
}
