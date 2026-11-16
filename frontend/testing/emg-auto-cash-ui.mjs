import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { default: CommercialServicesDetail } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialServicesDetail.tsx',
  )
  const result = {
    appointment_id: 'synthetic-visit', check_id: 'synthetic-check', context_token: 'synthetic-context',
    revision: 0, service_date: '2026-11-16', payment_mode: 'cash',
    service_plan: { kind: 'emg_ncs', method: 'llm', recommended_ids: ['ncs', 'emg'] },
    selected_ids: ['ncs', 'emg'],
    codes: [{ id: 'ncs', code: '95908', units: 1 }, { id: 'emg', code: '95886', units: 2 }],
    available_codes: [{ id: 'ncs', code: '95908', units: 1 }, { id: 'emg', code: '95886', units: 2 }],
    cash_schedule: { packages: [{ id: 'full', label: 'Comprehensive Full Study - Arms & Legs', amount_cents: 130000 }] },
    cash_quote: { selection: 'full', selection_source: 'appointment_scope', min_cents: 130000 },
    price_range: { mode: 'services', lines: [] },
  }
  const render = (source) => renderToStaticMarkup(React.createElement(CommercialServicesDetail, {
    result: { ...result, cash_quote: { ...result.cash_quote, selection_source: source } },
    model: { readOnly: false, busy: false, recovering: false, saveReview: async () => true,
      setNotice: () => {}, anticipatedService: 'EMG 4 Areas' },
  }))
  assert(render('appointment_scope').includes('AI planning CPTs'))
  assert(render('appointment_scope').includes('CPT 95908'))
  assert(!render('appointment_scope').includes('Cash package'))
  assert(!render('appointment_scope').includes('Recalculate estimate'))
  assert(render('clinician').includes('Cash package'))
  const { default: CommercialOverview } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialOverview.tsx',
  )
  const { deriveCommercialEstimateView } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/deriveCommercialEstimateView.ts',
  )
  const priced = { ...result, reason: 'EMG 4 Areas', visit_type: 'established',
    benefits: { active: false }, identity: {},
    collection_guidance: { amount_cents: 130000, patient_actions: [], assumptions: [], services: [] },
    price_range: { mode: 'services', complete: true, cash_min_cents: 130000, cash_max_cents: 130000,
      lines: [{ id: 'ncs', code: '95908', units: 1, benefits: {} },
        { id: 'emg', code: '95886', units: 2, benefits: {} }] },
  }
  const view = deriveCommercialEstimateView({ result: priced, appointments: [], selectedVisit: null,
    busy: null, error: '', readOnly: false, recovering: false })
  const overview = renderToStaticMarkup(React.createElement(CommercialOverview, {
    result: priced, model: { ...view, readOnly: false, cash: true, result: priced },
  }))
  assert(overview.includes('1,300'))
  assert(overview.includes('AI planning CPTs: CPT 95908, CPT 95886'))
  assert(!overview.includes('Review planned CPTs'))
  console.log('EMG automatic cash package UI: passed')
} finally {
  await server.close()
}
