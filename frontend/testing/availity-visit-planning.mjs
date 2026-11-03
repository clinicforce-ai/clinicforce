import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { AvailityArrivalAmount, AvailityVisitTable } =
    await server.ssrLoadModule('/src/features/commercial-estimate/AvailityVisitPlanning.tsx')
  const { availityVisitPlanning } =
    await server.ssrLoadModule('/src/features/commercial-estimate/availityVisitPlanning.ts')
  const result = {
    service_date: '2026-11-03', reason: 'Synthetic specialist office visit',
    codes: [{ code: '99203', units: 1, modifiers: [] }],
    availity_visit_reference: {
      source: 'Availity Essentials eligibility PDF', service_date: '2026-11-03',
      active: true, network_status: 'in_network', plan: 'Aetna Medicare Signature (PPO)',
      copay_cents: 7000, coinsurance_percent: 0,
      oop_annual_cents: 675000, oop_remaining_cents: 392050,
    },
    temporary_rate_reference: {
      source: 'Aetna MEPPO portal table values supplied by clinic',
      lines: [{ code: '99203', units: 1, unit_reference_cents: 11960 }],
    },
    collection_guidance: { amount_cents: null },
  }
  const model = { cash: false, previousEstimate: false, cashPrice: '$250.00' }
  assert(availityVisitPlanning(result, model))
  const table = renderToStaticMarkup(React.createElement(AvailityVisitTable, { result, model }))
  for (const expected of ['$119.60', '$70.00', '$3,920.50', '0%', 'Pending QMB'])
    assert(table.includes(expected), `Missing ${expected}`)
  assert.match(table, /Amount to collect at visit<\/th><td><span>\$70\.00<\/span><\/td>/)
  assert(!table.includes('$73.83</span>'))
  const summary = renderToStaticMarkup(React.createElement(AvailityArrivalAmount, { result }))
  assert(summary.includes('$70.00'))
  assert(summary.includes('pending verification'))
  const protectedResult = { ...result, billing_protection: { status: 'qmb' } }
  const protectedSummary = renderToStaticMarkup(React.createElement(AvailityArrivalAmount, {
    result: protectedResult,
  }))
  assert(protectedSummary.includes('$0.00'))
  assert(!availityVisitPlanning({ ...result, service_date: '2026-11-04' }, model))
} finally {
  await server.close()
}
