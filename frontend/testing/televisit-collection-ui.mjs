import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { deriveCommercialEstimateView } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/deriveCommercialEstimateView.ts',
  )
  const { default: CommercialCostBreakdown } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialCostBreakdown.tsx',
  )
  const identity = { status: 'matched', match_version: 2, binding: 'synthetic' }
  const benefits = {
    active: true, service_date_matches: true, network_status: 'in_network',
    network_source: 'clinic_group_agreement', identity, oop_remaining_cents: 0,
    review_reasons: [], reported_options: [],
  }
  const line = { id: 'visit', code: '99213', units: 1, modifiers: [],
    allowed_cents: null, calculation: null, benefits }
  const result = {
    payer: 'Synthetic Blue', reason: 'Televisit', service_date: '2026-10-29',
    payment_mode: 'insurance', identity, benefits, issues: [], codes: [line],
    selected_ids: ['visit'], service_selection_status: 'preview',
    service_plan: { method: 'history', recommended_ids: ['visit'] },
    price_range: { mode: 'services', complete: false, lines: [line] },
    collection_guidance: { status: 'estimated', basis: 'out_of_pocket_limit',
      amount_cents: 0, services: [], reason: 'Covered care has no remaining in-network out-of-pocket balance.' },
    encounter_summary: { collect_today_cents: 0, complete: false, unpriced_codes: ['99213'] },
  }
  const model = deriveCommercialEstimateView({
    result, appointments: [], selectedVisit: '', busy: null, error: '', readOnly: false, recovering: false,
  })
  assert.equal(model.amountText, '$0.00')
  const table = renderToStaticMarkup(React.createElement(CommercialCostBreakdown, { model, result }))
  assert(table.includes('Amount to collect at visit</th><td><span>$0.00'))
  assert(table.includes('99213'))
  assert(table.includes('Not fully priced'))

  const unresolved = { ...result,
    price_range: { ...result.price_range, service_issues: ['Confirm the patient telehealth location.'] },
    collection_guidance: { ...result.collection_guidance, status: 'action_required',
      basis: 'missing_collection_inputs', amount_cents: null },
    encounter_summary: { ...result.encounter_summary, collect_today_cents: null },
  }
  const unresolvedModel = deriveCommercialEstimateView({
    result: unresolved, appointments: [], selectedVisit: '', busy: null, error: '', readOnly: false, recovering: false,
  })
  assert.notEqual(unresolvedModel.amountText, '$0.00')
  const unresolvedTable = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
    { model: unresolvedModel, result: unresolved }))
  assert(unresolvedTable.includes('Amount to collect at visit</th><td><span>Not established'))
} finally {
  await server.close()
}
