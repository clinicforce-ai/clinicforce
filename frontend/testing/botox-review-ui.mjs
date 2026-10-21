import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { ServicePlanner } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/ServicePlanner.tsx',
  )
  const result = {
    check_id: 'synthetic', context_token: 'a'.repeat(64), service_date: '2026-10-14',
    visit_type: 'established', payment_mode: 'insurance', codes: [], selected_ids: [],
    service_plan: { kind: 'botox', recommended_ids: [] },
    available_codes: [
      { id: 'drug', code: 'J0585', units: null, source: 'CPT planning catalog' },
      { id: 'injection', code: '64612', units: 1, source: 'CPT planning catalog' },
      { id: 'office', code: '99214', units: 1, source: 'CPT planning catalog' },
    ],
    price_range: { complete: false, lines: [] }, botox_review: {},
  }
  const html = renderToStaticMarkup(React.createElement(ServicePlanner, {
    result, busy: false, save: async () => true, viewEstimate: () => {},
  }))
  for (const phrase of [
    'No services selected', 'Injection and medication', 'J0585', '64612',
    'Botox clinical review', 'Documented medical indication', 'Drug source',
  ]) assert(html.includes(phrase), phrase)
  const selected = renderToStaticMarkup(React.createElement(ServicePlanner, {
    result: { ...result, selected_ids: ['drug', 'injection', 'office'], service_selection_status: 'approved',
      codes: result.available_codes.map((row) => ({ ...row, units: row.code === 'J0585' ? 80 : 1 })),
      botox_review: { encounter_status: 'planned', planned_units: 80 },
      benefits: { reported_options: [{ id: 'synthetic-row', kind: 'coinsurance',
        service_type_code: '1', network: 'Y', time_period: 'VISIT', percent: '20' }] },
    },
    busy: false, save: async () => true, viewEstimate: () => {},
  }))
  for (const phrase of ['Planned HCPCS units',
                        'Separate office evaluation is documented',
                        'Review evidence for each selected service', 'Medical benefit and cost share',
                        'Save line verification'])
    assert(selected.includes(phrase), phrase)
  assert(!html.includes('200 units administered'))
  console.log('Botox planner review UI: passed')
} finally {
  await server.close()
}
