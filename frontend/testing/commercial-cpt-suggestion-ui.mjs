import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { default: CommercialCostBreakdown } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialCostBreakdown.tsx',
  )
  const html = renderToStaticMarkup(React.createElement(CommercialCostBreakdown, {
    model: { cash: false, networkLabel: 'In network', policyStatus: 'Active' },
    result: {
      payer: 'Synthetic payer', reason: 'Botox injection', service_date: '2026-10-29',
      service_plan: { kind: 'botox', recommended_ids: [] },
      service_selection_status: 'preview', selected_ids: [], codes: [],
      available_codes: [
        { id: 'candidate', code: '64612' },
        { id: 'retired-1', code: '64613' },
        { id: 'retired-2', code: '64614' },
      ],
      suggestions: { groups: [{ label: 'Injection', codes: [
        { code: '64612' }, { code: '64613' }, { code: '64614' },
      ] }], notes: [] },
      benefits: { active: true, network_status: 'in_network' },
      price_range: { mode: 'services', lines: [], complete: false },
      collection_guidance: { amount_cents: null },
    },
  }))
  assert(html.includes('Coding assistant candidates'))
  assert(html.includes('CPT 64612'))
  assert(!html.includes('CPT 64613'))
  assert(!html.includes('CPT 64614'))
  assert(html.includes('No CPTs selected'))
  console.log('Commercial CPT suggestion UI: passed')
} finally {
  await server.close()
}
