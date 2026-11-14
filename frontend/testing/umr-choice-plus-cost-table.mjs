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
  const oop = (coverage_level, time_period, amount_cents, payer_note = '') => ({
    kind: 'out_of_pocket', service_type_code: '98', coverage_level, time_period,
    amount_cents, payer_note, network: 'IN_NETWORK', plan_association: 'medical',
    plan_index: 0, insurance_type: 'GROUP_POLICY',
    effective_dates: { benefit: time_period === 'REMAINING' ? '2026-01-01 to 2026-12-31' : '2026-06-01 to end not returned' },
  })
  const rows = []
  for (const [level, annual, remaining] of [
    ['INDIVIDUAL', 500000, 337064], ['FAMILY', 1150000, 491164],
  ]) for (const note of ['', 'PHY VISIT - SPECIALIST']) {
    rows.push(oop(level, 'CALENDAR_YEAR', annual, note), oop(level, 'REMAINING', remaining, note))
  }
  const identity = { status: 'matched', match_version: 2, binding: 'synthetic-person' }
  const benefits = { active: true, service_date_matches: null, identity,
    network_status: 'in_network', network_source: 'clinic_library_contract',
    oop_remaining_cents: 337064, family_oop_remaining_cents: 491164,
    reported_options: rows }
  const terms = { ...benefits, copay_cents: 5000, copay_period: 'VISIT',
    deductible_applies: false, coinsurance_percent: '0' }
  const result = {
    payer: 'United Healthcare', service_date: '2026-11-14', reason: 'Synthetic follow-up',
    identity, benefits, plan_details: { stedi: { names: [],
      network_names: ['UNITEDHEALTHCARE CHOICE PLUS'], group_number: 'SYNTHETIC-GROUP',
      group_name: 'Synthetic employee benefit plan', response_payer_name: 'UMR' }, ecw: { plan: ['CI'] } },
    insurance_details: { member_id: 'SYNTHETIC-MEMBER', group_number: 'SYNTHETIC-GROUP' },
    service_selection_status: 'preview', service_plan: { method: 'llm', recommended_ids: ['office'] },
    codes: [{ id: 'office', code: '99213', units: 1 }],
    price_range: { mode: 'services', complete: true, conditional: false,
      allowed_min_cents: 8178, allowed_max_cents: 8178,
      patient_min_cents: 5000, patient_max_cents: 5000, lines: [{
        id: 'office', code: '99213', units: 1, allowed_cents: 8178, unit_allowed_cents: 8178,
        allowed_basis: 'payer_fee_schedule', rate_resolution: { status: 'RESOLVED' },
        benefits: terms, calculation: { patient_cents: 5000, plan_cents: 3178,
          copay_cents: 5000, deductible_cents: 0, coinsurance_cents: 0,
          allowed_cap_adjustment_cents: 0, oop_adjustment_cents: 0 },
      }] },
    encounter_summary: { complete: true, collect_today_cents: 5000,
      estimated_total_patient_min_cents: 5000, estimated_total_patient_max_cents: 5000,
      components: { copay_cents: 5000, deductible_cents: 0, coinsurance_cents: 0,
        allowed_cap_adjustment_cents: 0, oop_adjustment_cents: 0 } },
    collection_guidance: { status: 'estimated', basis: 'office_copay', amount_cents: 5000, services: [],
      collection_rule: { mode: 'calculated_in_network_advance' } },
  }
  const model = deriveCommercialEstimateView({ result, appointments: [], selectedVisit: '',
    busy: null, error: '', readOnly: true, recovering: false })
  assert.equal(model.amount, '$50.00')
  assert.equal(model.supportedAmount, true)
  const table = renderToStaticMarkup(React.createElement(Table, { model, result }))
  const headline = renderToStaticMarkup(React.createElement(Headline, {
    model, result, partialCopay: null, visibleAmount: true,
  }))
  const visible = [...table.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/g)]
    .map(([, html]) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim())
  const row = (label) => visible.find((value) => value.startsWith(label)) || ''
  assert(headline.includes('$50.00') && !headline.includes('Check CPT prices'))
  assert(row('Coverage').includes('UnitedHealthcare Choice Plus network · UMR eligibility response'))
  assert(row('LLM-proposed CPTs').includes('CPT 99213 × 1'))
  assert(row('Total payer allowed').includes('$81.78') && row('Total payer allowed').toLowerCase().includes('clinic-approved'))
  assert(row('Individual deductible').includes('Does not apply to selected CPTs'))
  assert(row('Coinsurance').includes('0% · $0.00'))
  assert(row('Individual out-of-pocket maximum').includes('$5,000.00 annual · $3,370.64 remaining'))
  assert(row('Family out-of-pocket maximum').includes('$11,500.00 annual · $4,911.64 remaining'))
  assert(row('Estimated patient share').includes('$50.00'))
  assert(row('Amount to collect at visit').includes('$50.00') &&
    row('Amount to collect at visit').includes('$31.78 estimated plan share'))
  const conflicting = { ...result, benefits: { ...benefits,
    reported_options: [...rows, oop('FAMILY', 'REMAINING', 400000)] } }
  const conflictingMarkup = renderToStaticMarkup(React.createElement(Table, { model, result: conflicting }))
  assert(!conflictingMarkup.includes('$4,911.64 remaining'))
  console.log('UMR Choice Plus cost table: pass')
} finally {
  await server.close()
}
