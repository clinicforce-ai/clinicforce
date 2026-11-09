import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { rolldown } = require('rolldown')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const bundle = await rolldown({
  input: new URL('../frontend/src/features/commercial-estimate/CommercialCostBreakdown.tsx', import.meta.url).pathname,
  plugins: [{ name: 'ignore-styles',
    resolveId(source) { if (source.endsWith('.css')) return '\0empty-style' },
    load(id) { if (id === '\0empty-style') return '' },
  }],
})
const built = await bundle.generate({ format: 'esm' })
const { default: CommercialCostBreakdown } = await import(
  `data:text/javascript;base64,${Buffer.from(built.output[0].code).toString('base64')}`
)

const result = {
  payer: 'United Healthcare', service_date: '2026-11-09', reason: 'Televisit',
  identity: { status: 'matched', match_version: 2, binding: 'synthetic-binding' },
  benefits: { active: true, service_date_matches: true, network_status: 'in_network',
    network_source: 'clinic_library_contract', deductible_annual_cents: 0,
    deductible_remaining_cents: 0, coinsurance_percent: '0', oop_annual_cents: 100000,
    oop_remaining_cents: 100000, published_plan_rule: {
      label: 'ERS HealthSelect Medicare Advantage plan benefits',
      url: 'https://ers.texas.gov/retirees/health-benefits/healthselect-medicare-advantage',
    } },
  plan_details: { stedi: { names: ['LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE (PP'],
    group_number: '13546', plan_number: 'H2001-817-000' }, ecw: { plan: [] } },
  card_check: { status: 'matched', cms_plan_id: 'H2001-817', office_copay_cents: 0 },
  insurance_details: { member_id: 'SYNTHETIC123', group_number: '13546' },
  price_range: { mode: 'services', complete: false, conditional: false, lines: [
    { id: 'visit', code: '98006', units: 1, description: 'Prior historical telehealth code',
      allowed_cents: null, benefits: { active: true, service_date_matches: true,
        copay_cents: 0, coinsurance_percent: '0' } },
  ] },
  service_selection_status: 'preview', service_plan: { method: 'llm', recommended_ids: ['visit'] },
  collection_guidance: { amount_cents: 0, basis: 'healthselect_ma_zero_televisit', status: 'zero_advance',
    reason: 'No arrival payment for the proposed covered virtual visit.', services: [],
    patient_actions: [], assumptions: [] },
  encounter_summary: { collect_today_cents: 0, complete: false,
    estimated_total_patient_min_cents: null, payments_verified_cents: null },
  recovery: { reason_code: 'multiple_active_policies' },
}
const model = { cash: false, previousEstimate: false, calculating: false,
  planName: 'HealthSelect Medicare Advantage PPO', networkLabel: 'In network',
  policyStatus: 'Coverage active', anticipatedService: 'Televisit',
  supportedAmount: false, amount: null, estimatedShare: null, cashPrice: null }
const html = renderToStaticMarkup(React.createElement(CommercialCostBreakdown, { result, model }))
for (const fragment of ['HealthSelect MA PPO', 'H2001-817', 'CPT 98006 needs coding review',
  'Conditional patient share', '$0.00 copay + $0.00 deductible + 0%',
  'Amount to collect at visit', '$0.00 at check-in', 'Not required for this Group']) {
  assert.ok(html.includes(fragment), `Missing visit-table text: ${fragment}`)
}
assert.ok(!html.includes('No insured amount is ready'))
const viewBundle = await rolldown({
  input: new URL('../frontend/src/features/commercial-estimate/deriveCommercialEstimateView.ts', import.meta.url).pathname,
})
const viewBuilt = await viewBundle.generate({ format: 'esm' })
const { deriveCommercialEstimateView } = await import(
  `data:text/javascript;base64,${Buffer.from(viewBuilt.output[0].code).toString('base64')}`
)
const view = deriveCommercialEstimateView({ result, appointments: [], selectedVisit: null,
  busy: null, error: null, readOnly: false, recovering: false })
assert.equal(view.amountText, '$0.00')
assert.equal(view.amountLabel, 'Amount to collect now')
const saved = structuredClone(result)
saved.price_range.conditional = true
saved.price_range.lines[0].benefits.has_secondary = true
const savedTable = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
  { result: saved, model }))
assert.ok(savedTable.includes('Conditional patient share'))
assert.ok(savedTable.includes('$0.00 at check-in'))
const savedView = deriveCommercialEstimateView({ result: saved, appointments: [], selectedVisit: null,
  busy: null, error: null, readOnly: true, recovering: false })
assert.equal(savedView.amountText, '$0.00')
const independent = structuredClone(saved)
independent.card_check = { status: 'conflict', conflicts: ['member_id', 'group_number'],
  independent_ecw_check: true }
const independentView = deriveCommercialEstimateView({ result: independent, appointments: [],
  selectedVisit: null, busy: null, error: null, readOnly: false, recovering: false })
assert.equal(independentView.amountText, '$0.00')
const independentTable = renderToStaticMarkup(React.createElement(CommercialCostBreakdown,
  { result: independent, model: independentView }))
assert.ok(independentTable.includes('$0.00 at check-in'))
assert.ok(independentTable.includes('Saved card differs from the selected eCW policy'))
const summaryBundle = await rolldown({
  input: new URL('../frontend/src/features/commercial-estimate/ArrivalAmountSummary.tsx', import.meta.url).pathname,
})
const summaryBuilt = await summaryBundle.generate({ format: 'esm' })
const { default: ArrivalAmountSummary } = await import(
  `data:text/javascript;base64,${Buffer.from(summaryBuilt.output[0].code).toString('base64')}`
)
const savedSummary = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
  { result: saved, model: savedView, partialCopay: null, visibleAmount: true }))
assert.ok(savedSummary.includes('$0.00'))
assert.ok(!savedSummary.includes('Confirm payer order'))
console.log('HealthSelect MA virtual-visit table shows card, formula, conditional share and $0 arrival amount')
