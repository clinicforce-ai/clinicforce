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
  const { default: CommercialInsurerIdentity } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialInsurerIdentity.tsx',
  )
  const { payerPlanDisplay } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/payerPlanDisplay.ts',
  )
  const model = { cash: false, supportedAmount: false, amountText: 'Insured amount unavailable',
    planName: 'Optum Care', networkLabel: 'Not determined', policyStatus: 'Unknown' }
  const result = {
    payer: 'Optum Care', payment_mode: 'insurance', service_date: '2026-11-20',
    reason: 'Three-month follow-up',
    network_evidence: { place_of_service: '11' },
    card_check: { status: 'conflict', conflicts: ['member_id'], differences: ['payer_name'],
      published_oon_specialist_copay_cents: 8500,
      published_inn_specialist_copay_cents: 5000,
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
  assert(table.includes('$50.00 in-network / $85.00 out-of-network'))
  assert(table.includes('Not determined · verify provider network'))
  assert(!table.includes('$85.00 conditional plan reference'))
  assert(!table.includes('$150.00</span><small>'))
  const arrival = renderToStaticMarkup(React.createElement(ArrivalAmountSummary, {
    model, result, partialCopay: null, visibleAmount: true,
  }))
  assert(arrival.includes('Network tier not determined'))
  assert(arrival.includes('$50.00 in-network') && arrival.includes('$85.00 out-of-network'))
  const displayedPlan = payerPlanDisplay(result)
  assert(displayedPlan.planName.includes('UHC NM-0006'))
  assert(displayedPlan.planSource.includes('insurance card'))
  const insurer = renderToStaticMarkup(React.createElement(CommercialInsurerIdentity,
    { result, ...displayedPlan, payerSource: 'Coverage.payor',
      secondaryPayers: ['United Healthcare'] }))
  assert(insurer.includes('UnitedHealthcare Medicare Advantage PPO'))
  assert(insurer.includes('Optum Care administers this plan'))
  assert(insurer.includes('LIFE1'))
  assert(insurer.includes('eCW selected payor: Optum Care'))
  assert(insurer.includes('Other eCW entry'))
  const savedCardBeforeUpdate = { ...result, card_check: {
    ...result.card_check, published_inn_specialist_copay_cents: undefined,
  } }
  const savedInsurer = renderToStaticMarkup(React.createElement(CommercialInsurerIdentity,
    { result: savedCardBeforeUpdate, ...payerPlanDisplay(savedCardBeforeUpdate),
      payerSource: 'Coverage.payor', secondaryPayers: ['United Healthcare'] }))
  assert(savedInsurer.includes('UnitedHealthcare Medicare Advantage PPO'))
  const genericInsurer = renderToStaticMarkup(React.createElement(CommercialInsurerIdentity,
    { result: { ...result, card_check: { status: 'unavailable' } }, planName: 'Optum Care',
      planSource: 'eCW policy record', payerSource: 'Coverage.payor', secondaryPayers: [] }))
  assert(genericInsurer.includes('Optum Care'))
  assert(!genericInsurer.includes('UnitedHealthcare Medicare Advantage PPO'))
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
    assert(!blocked.includes('$50.00 in-network / $85.00 out-of-network'))
  }
  const cashFallbackResult = { ...result, cash_fallback: { reason: 'Search incomplete' },
    collection_guidance: { amount_cents: 15000, status: 'estimate', basis: 'practice_cash_price' } }
  const automaticCash = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
    { model: { ...model, cash: true }, result: cashFallbackResult }))
  assert(automaticCash.includes('$50.00 in-network / $85.00 out-of-network'))
  const automaticCashArrival = renderToStaticMarkup(React.createElement(ArrivalAmountSummary, {
    model: { ...model, cash: true }, result: cashFallbackResult,
    partialCopay: null, visibleAmount: true,
  }))
  assert(automaticCashArrival.includes('Network tier not determined'))
  assert(!automaticCashArrival.includes('$150.00'))
  const missingNetworkEvidence = { ...cashFallbackResult, network_evidence: undefined,
    appointment_context: { place_of_service: '11' } }
  const noNetworkTable = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
    { model: { ...model, cash: true }, result: missingNetworkEvidence }))
  assert(noNetworkTable.includes('$50.00 in-network / $85.00 out-of-network'))
  const noNetworkArrival = renderToStaticMarkup(React.createElement(ArrivalAmountSummary, {
    model: { ...model, cash: true }, result: missingNetworkEvidence,
    partialCopay: null, visibleAmount: true,
  }))
  assert(noNetworkArrival.includes('Network tier not determined'))
  const conflictingOfficeSetting = renderToStaticMarkup(React.createElement(ArrivalAmountSummary, {
    model: { ...model, cash: true }, result: { ...missingNetworkEvidence,
      network_evidence: { place_of_service: '02' } },
    partialCopay: null, visibleAmount: true,
  }))
  assert(!conflictingOfficeSetting.includes('Network tier not determined'))
  const explicitCash = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
    { model: { ...model, cash: true }, result }))
  assert(!explicitCash.includes('$50.00 in-network / $85.00 out-of-network'))
  const selectedCash = renderToStaticMarkup(React.createElement(ArrivalAmountSummary, {
    model: { ...model, cash: true, amountText: '$150.00', amountLabel: 'Self-pay price' },
    result: { ...result, payment_mode: 'cash',
      collection_guidance: { amount_cents: 15000, status: 'estimate', basis: 'practice_cash_price' } },
    partialCopay: null, visibleAmount: true,
  }))
  assert(selectedCash.includes('$150.00'))
  assert(!selectedCash.includes('Network tier not determined'))
  console.log('UHC NM-0006 issuer and tier references: unknown network stays uncollectable')
} finally {
  await server.close()
}
