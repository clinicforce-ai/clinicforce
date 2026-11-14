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
  const identity = { status: 'matched', match_version: 2, binding: 'x' }
  const plan = { names: [], network_names: ['UNITEDHEALTHCARE CHOICE PLUS'] }
  const benefits = {
    active: true, identity, service_date_matches: true, has_secondary: false,
    network_status: 'in_network', network_source: 'clinic_library_contract', plan_details: plan,
  }
  const terms = {
    ...benefits, copay_cents: 5000, copay_id: 'specialist', copay_period: 'VISIT',
    deductible_applies: false, coinsurance_percent: '0', oop_remaining_cents: 337064,
  }
  const result = {
    payer: 'United Healthcare', service_date: '2026-11-14', reason: 'Follow-up',
    payment_mode: 'insurance', benefit_source: 'Stedi', identity, benefits,
    plan_details: { ecw: { plan: ['CI'] }, stedi: plan }, insurance_details: {},
    codes: [{ id: 'office', code: '99213', units: 1 }],
    price_range: {
      mode: 'services', complete: true, conditional: false,
      patient_min_cents: 5000, patient_max_cents: 5000,
      allowed_min_cents: 8178, allowed_max_cents: 8178,
      lines: [{ id: 'office', code: '99213', units: 1, allowed_cents: 8178,
        allowed_basis: 'payer_fee_schedule', benefits: terms,
        rate_resolution: { status: 'RESOLVED' },
        calculation: { patient_cents: 5000, plan_cents: 3178 } }],
    },
    collection_guidance: {
      status: 'estimated', basis: 'office_copay', amount_cents: 5000,
      services: [], patient_actions: [], assumptions: [], reason: 'Specialist office copay',
    },
    encounter_summary: { collect_today_cents: 5000 },
  }
  const render = (estimate) => {
    const model = deriveCommercialEstimateView({
      result: estimate, appointments: [], selectedVisit: '', busy: null, error: '',
      readOnly: true, recovering: false,
    })
    return { model, html: renderToStaticMarkup(
      React.createElement(CommercialCostBreakdown, { model, result: estimate }),
    ) }
  }
  const ready = render(result)
  assert.equal(ready.model.amount, '$50.00')
  assert.match(ready.html, /Amount to collect at visit<\/th><td><span>\$50\.00/)
  assert.match(ready.html, /<details class="cost-breakdown"[^>]* open=""/)

  const pending = structuredClone(result)
  pending.collection_guidance.amount_cents = null
  pending.collection_guidance.status = 'action_required'
  const unresolved = render(pending)
  assert.equal(unresolved.model.amount, null)
  assert.doesNotMatch(unresolved.html, /<details class="cost-breakdown"[^>]* open=""/)
  console.log('PASS: supported Choice Plus specialist collection opens the saved visit table')
} finally {
  await server.close()
}
