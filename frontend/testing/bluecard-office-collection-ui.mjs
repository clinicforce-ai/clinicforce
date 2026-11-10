import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { default: ArrivalAmountSummary } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/ArrivalAmountSummary.tsx',
  )
  const result = {
    payer: 'Blue Cross Blue Shield Tx',
    plan_details: { stedi: { names: ['Preferred Provider Organization'] } },
    identity: { status: 'matched', binding: 'synthetic-current-policy', match_version: 2 },
    benefits: { active: true, network_status: 'unknown', oop_remaining_cents: 429053 },
    collection_guidance: { amount_cents: null, positive_advance_policy_missing: true },
    price_range: { mode: 'services', lines: [{ code: '99203', units: 1, modifiers: [],
      benefits: { active: true, network_status: 'unknown', copay_cents: 6000,
        copay_period: '27', deductible_applies: false, coinsurance_percent: '0',
        oop_remaining_cents: 429053 } }] },
  }
  const model = { cash: false, calculating: false, previousEstimate: false,
    amount: null, requirementIssue: true, networkLabel: 'Contract match unavailable',
    amountText: 'Insured amount unavailable', guidance: result.collection_guidance }
  const html = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
    { result, model, partialCopay: null, visibleAmount: false }))
  assert.match(html, /Pending · \$60\.00/)
  assert.match(html, /Confirm BlueCard provider participation/)
  assert.doesNotMatch(html, /Approve collection policy/)
  const blocked = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
    { result: { ...result, identity: { status: 'review' } }, model,
      partialCopay: null, visibleAmount: false }))
  assert.match(blocked, /Verify network/)
  const metLimit = { ...result, price_range: { ...result.price_range,
    lines: [{ ...result.price_range.lines[0], benefits: {
      ...result.price_range.lines[0].benefits, oop_remaining_cents: 0,
    } }] } }
  const metHtml = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
    { result: metLimit, model, partialCopay: null, visibleAmount: false }))
  assert.doesNotMatch(metHtml, /Pending · \$60\.00/)
} finally {
  await server.close()
}
