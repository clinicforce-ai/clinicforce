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
    payer: 'Synthetic PPO',
    benefits: { active: true, network_status: 'unknown' },
    price_range: { mode: 'services', lines: [{
      code: '99203', units: 1, rate_resolution: { status: 'RESOLVED' },
      benefits: { copay_cents: 6000 },
    }] },
    collection_guidance: { amount_cents: null, policy_setup_required: true },
  }
  const baseModel = {
    cash: false, previousEstimate: false, calculating: false,
    guidance: { policy_setup_required: true },
    amountText: 'Insured amount unavailable',
  }
  const render = (networkLabel) => renderToStaticMarkup(
    React.createElement(ArrivalAmountSummary, {
      model: { ...baseModel, networkLabel }, result, partialCopay: null, visibleAmount: false,
    }),
  )
  assert.match(render('Contract match unavailable'), /Verify network/)
  assert.doesNotMatch(render('Contract match unavailable'), /Approve collection policy/)
  assert.match(render('In network'), /Approve collection policy/)
} finally {
  await server.close()
}
