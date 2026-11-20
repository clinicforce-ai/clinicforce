import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { deriveCommercialEstimateView } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/deriveCommercialEstimateView.ts',
  )
  const { default: Table } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialCostBreakdown.tsx',
  )
  const { default: Headline } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/ArrivalAmountSummary.tsx',
  )
  const identity = { status: 'matched', match_version: 2, binding: 'synthetic' }
  const benefits = {
    active: true, identity, service_date_matches: null, network_status: 'in_network',
    network_source: 'clinic_library_contract', copay_cents: 0,
    plan_details: { names: ['POS CHOICE PLUS'], supplementary_names: ['SPECTERA VISION PLAN'] },
  }
  const result = {
    payer: 'UnitedHealthcare', service_date: '2026-11-20', reason: 'Follow Up',
    payment_mode: 'insurance', identity, benefits,
    plan_details: { stedi: benefits.plan_details },
    codes: [{ id: 'office', code: '99213', units: 1 }], selected_ids: ['office'],
    price_range: { mode: 'services', complete: false, lines: [{
      id: 'office', code: '99213', units: 1, allowed_cents: null,
      rate_resolution: { status: 'RATE_UNAVAILABLE' },
      benefits: { active: true, copay_cents: 0, coinsurance_percent: '20' },
    }] },
    collection_guidance: {
      amount_cents: 0, status: 'zero_advance', basis: 'claim_after_service',
      reason: 'Collect $0 at check-in; medical limit applicability is unconfirmed.',
      collection_rule: { mode: 'uhc_choice_plus_family_oop_deferral' }, services: [],
    },
    encounter_summary: { collect_today_cents: 0 },
  }
  const model = deriveCommercialEstimateView({ result, appointments: [], selectedVisit: '',
    busy: null, error: '', readOnly: true, recovering: false })
  const table = renderToStaticMarkup(React.createElement(Table, { model, result }))
  const headline = renderToStaticMarkup(React.createElement(Headline, {
    model, result, partialCopay: null, visibleAmount: true,
  }))
  assert.equal(model.amount, '$0.00')
  assert.match(table, /Amount to collect at visit<\/th><td><span>\$0\.00/)
  assert.match(headline, /Amount to collect at visit/)
  assert.match(headline, /\$0\.00/)
  assert.doesNotMatch(headline, /Check CPT prices|Verify network/)
  const unknown = { ...result, benefits: { ...benefits, network_status: 'unknown', network_source: null } }
  const unknownModel = deriveCommercialEstimateView({ result: unknown, appointments: [], selectedVisit: '',
    busy: null, error: '', readOnly: true, recovering: false })
  const unknownTable = renderToStaticMarkup(React.createElement(Table, { model: unknownModel, result: unknown }))
  const unknownHeadline = renderToStaticMarkup(React.createElement(Headline, {
    model: unknownModel, result: unknown, partialCopay: null, visibleAmount: true,
  }))
  assert.match(unknownTable, /Amount to collect at visit<\/th><td><span>\$0\.00/)
  assert.match(unknownHeadline, /\$0\.00/)
  assert.doesNotMatch(unknownHeadline, /Verify network|Check CPT prices/)
  console.log('Choice Plus 99213 shows a $0 check-in advance while the CPT rate is missing')
} finally {
  await server.close()
}
