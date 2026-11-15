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
  const model = { cash: false, previousEstimate: false, amountText: 'Insured amount unavailable',
    networkLabel: 'In network', supportedAmount: false }
  const result = {
    payer: 'Humana Health Plan', service_date: '2026-11-16', payment_mode: 'insurance',
    identity: { status: 'matched' }, billing_protection: {},
    plan_details: { stedi: { names: ['Synthetic Humana Honor'], medicare_ppo_evidence: 'synthetic' } },
    benefits: { active: true, service_date_matches: true, network_status: 'in_network',
      network_source: 'clinic_library_contract', coverage_status_conflict: false },
    price_range: { mode: 'services', complete: false, lines: [{ id: 'office', code: '99203',
      units: 1, allowed_cents: 11362, allowed_basis: 'payer_fee_schedule',
      rate_resolution: { status: 'RESOLVED' }, benefits: { active: true, copay_cents: 4000,
        copay_period: 'VISIT', coinsurance_percent: '0', deductible_applies: null,
        oop_remaining_cents: 423799, source_errors: [] } }] },
    collection_guidance: { amount_cents: null, basis: 'missing_collection_inputs' },
  }
  const headline = (current) => renderToStaticMarkup(React.createElement(
    ArrivalAmountSummary, { model, result: current, partialCopay: null, visibleAmount: true },
  ))
  assert.match(headline(result), /Pending · \$40\.00/)
  assert.match(headline(result), /Verify QMB protection/)
  assert.match(renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
    { model, result })), /Pending · \$40\.00 payer-reported specialist copay/)
  for (const unsafe of [
    { ...result, identity: { status: 'review' } },
    { ...result, billing_protection: { status: 'qmb' } },
    { ...result, benefits: { ...result.benefits, has_secondary: true } },
    { ...result, benefits: { ...result.benefits, network_conflict: true } },
    { ...result, price_range: { ...result.price_range,
      lines: [{ ...result.price_range.lines[0], rate_resolution: { status: 'MISSING' } }] } },
  ]) assert.doesNotMatch(headline(unsafe), /Pending · \$40\.00/)
  console.log('Humana specialist headline: passed')
} finally {
  await server.close()
}
