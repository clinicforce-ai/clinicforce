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
  const result = {
    service_date: '2026-10-29', payer: 'Synthetic Blue Plan', reason: 'Botox received from pharmacy',
    benefits: { active: true, network_status: 'in_network', reported_options: [] },
    service_plan: { kind: 'botox', recommended_ids: [] }, service_selection_status: 'preview',
    suggestions: { groups: [{ codes: [{ code: '64612' }, { code: '64613' }] }] },
    available_codes: [{ code: '64612', id: 'candidate' }],
    candidate_requirements: [{ code: '64612', id: 'candidate',
      referral: { status: 'Not required' }, authorization: { status: 'Not required' } }],
    price_range: { complete: false, lines: [] },
    botox_preview: { status: 'provisional', code: '64612', units: 1,
      allowed_cents: 13114, coinsurance_percent: '20', family_deductible_remaining_cents: 0,
      family_oop_remaining_cents: 128382, patient_share_cents: 2623,
      rate_source: 'Synthetic clinic fee row', benefit_source: 'Stedi office-injection benefit' },
  }
  const model = { cash: false, previousEstimate: false, networkLabel: 'In network',
    planName: 'Synthetic PPO', policyStatus: 'Active', supportedAmount: false,
    amountText: 'Insured amount unavailable' }
  const markup = renderToStaticMarkup(React.createElement(CommercialCostBreakdown, { model, result }))
  for (const phrase of ['Coding assistant candidates', 'CPT 64612', '$131.14', '20%', '$26.23',
    'provisional', 'Synthetic clinic fee row', 'candidate: not required']) assert(markup.includes(phrase), phrase)
  assert(!markup.includes('CPT 64613'))
  assert(!markup.includes('Not established'))
  const summary = renderToStaticMarkup(React.createElement(ArrivalAmountSummary, {
    model, result, partialCopay: null, visibleAmount: true,
  }))
  assert(summary.includes('$26.23') && summary.includes('Confirm the coding'))
  console.log('Botox provisional collection UI: passed')
} finally {
  await server.close()
}
