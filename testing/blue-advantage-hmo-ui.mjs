import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { rolldown } = require('rolldown')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')

async function bundled(path) {
  const bundle = await rolldown({
    input: new URL(path, import.meta.url).pathname,
    plugins: [{ name: 'ignore-css', load(id) {
      if (id.endsWith('.css')) return { code: 'export default {}', moduleType: 'js' }
    } }],
  })
  const compiled = await bundle.generate({ format: 'esm' })
  return import(`data:text/javascript;base64,${Buffer.from(compiled.output[0].code).toString('base64')}`)
}

const CommercialCostBreakdown = (await bundled('../frontend/src/features/commercial-estimate/CommercialCostBreakdown.tsx')).default
const ArrivalAmountSummary = (await bundled('../frontend/src/features/commercial-estimate/ArrivalAmountSummary.tsx')).default
const { deriveCommercialEstimateView } = await bundled('../frontend/src/features/commercial-estimate/deriveCommercialEstimateView.ts')
const result = {
  payer: 'Blue Cross Blue Shield Tx', service_date: '2026-11-18',
  identity: { status: 'matched', binding: 'synthetic-member', match_version: 2 },
  selected_ids: ['office'],
  plan_details: { stedi: { names: ['Blue Advantage HMO'] } },
  insurance_details: { group_number: 'SYNTHETIC' },
  benefits: { active: true, service_date_matches: true, network_status: 'in_network',
    network_source: 'clinic_group_agreement', deductible_annual_cents: 600000,
    deductible_remaining_cents: 0, family_deductible_annual_cents: 1200000,
    family_deductible_remaining_cents: 600000, oop_annual_cents: 1015000,
    oop_remaining_cents: 2348, family_oop_annual_cents: 2030000,
    family_oop_remaining_cents: 1017348, coinsurance_percent: '50' },
  price_range: { mode: 'services', complete: false, lines: [{
    id: 'office', code: '99203', units: 1, modifiers: [], allowed_cents: null,
    rate_resolution: { status: 'RATE_UNAVAILABLE' },
    benefits: { active: true, network_status: 'in_network', copay_cents: null,
      coinsurance_percent: '50', deductible_remaining_cents: 0, oop_remaining_cents: 2348 },
  }] },
  temporary_rate_reference: { complete: true, source: 'BCBS Texas clinic schedule', lines: [{
    id: 'office', code: '99203', units: 1, modifiers: [], unit_reference_cents: 10117,
    source_reference: 'library:synthetic-blue',
  }] },
  collection_guidance: { amount_cents: 2348, status: 'estimated', basis: 'bcbstx_refundable_advance',
    services: [],
    reason: 'Collect a refundable Blue Advantage HMO office advance: 50% of the $101.17 clinic Blue Cross fee reference is $50.59, capped by the payer-reported $23.48 remaining individual medical out-of-pocket balance.',
    collection_rule: { mode: 'blue_advantage_hmo_oop_deposit' } },
  encounter_summary: { collect_today_cents: 2348, payments_verified_cents: 0 },
}
let model
try {
  model = deriveCommercialEstimateView({ result, appointments: [], selectedVisit: '', busy: null,
    error: '', readOnly: false, recovering: false })
} catch (error) {
  console.error(error.message)
  process.exit(1)
}
assert.equal(model.supportedAmount, true)
assert.equal(model.amount, '$23.48')
assert.equal(model.amountLabel, 'Amount to collect at visit')
const table = renderToStaticMarkup(React.createElement(CommercialCostBreakdown, { result, model }))
assert.match(table, /Blue Cross fee reference/)
assert.match(table, /\$101\.17/)
assert.match(table, /50% reported/)
assert.match(table, /\$23\.48 remaining/)
assert.match(table, /Estimated patient share/)
assert.match(table, /Not fully calculated/)
assert.match(table, /Amount to collect at visit/)
assert.match(table, /\$23\.48/)
assert.doesNotMatch(table, /The payer returned no financial terms/)
const arrival = renderToStaticMarkup(React.createElement(ArrivalAmountSummary, {
  result, model, partialCopay: null, visibleAmount: true,
}))
assert.match(arrival, /Amount to collect at visit/)
assert.match(arrival, /\$23\.48/)
assert.doesNotMatch(arrival, /Check CPT prices/)
console.log('Blue Advantage HMO table: $23.48 refundable collection, fee and payer allowance separate')
