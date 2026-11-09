import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { rolldown } = require('rolldown')
async function load(path) {
  const bundle = await rolldown({ input: new URL(path, import.meta.url).pathname,
    plugins: [{ name: 'ignore-css', load(id) { if (id.endsWith('.css')) return { code: 'export default {}', moduleType: 'js' } } }] })
  const compiled = await bundle.generate({ format: 'esm' })
  return import(`data:text/javascript;base64,${Buffer.from(compiled.output[0].code).toString('base64')}`)
}
const { default: CommercialCostBreakdown } = await load('../frontend/src/features/commercial-estimate/CommercialCostBreakdown.tsx')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
const codes = [['ncs', '95908', 10511], ['emg', '95886', 8918]]
const procedures = codes.map(([id, code]) => ({ id, code,
  referral: { status: 'Not required', reason: 'UHC Choice Plus specialist access' },
  authorization: { status: 'Not required', reason: 'Current UHC commercial list' } }))
const result = {
  payer: 'UnitedHealthcare', service_date: '2026-11-09', reason: 'R Hand EMG',
  identity: { status: 'matched', match_version: 2, binding: 'synthetic' },
  plan_details: { stedi: { names: ['UNITEDHEALTHCARE CHOICE PLUS'] } },
  benefits: { active: true, service_date_matches: true, network_status: 'in_network',
    network_source: 'clinic_library_contract', deductible_remaining_cents: 330309,
    oop_remaining_cents: 455369, coinsurance_percent: '20', copay_cents: 0 },
  codes: codes.map(([id, code]) => ({ id, code, units: 1, modifiers: [] })),
  price_range: { mode: 'services', lines: codes.map(([id, code]) => ({ id, code, units: 1,
    modifiers: [], benefits: { active: true, network_status: 'in_network',
      copay_cents: 0, coinsurance_percent: '20', deductible_remaining_cents: 330309 } })) },
  temporary_rate_reference: { complete: true, source: 'NMCT Clinical Library Blue Cross fee schedule',
    lines: codes.map(([id, code, cents]) => ({ id, code, units: 1, modifiers: [],
      unit_reference_cents: cents, source_reference: 'library:synthetic' })) },
  fee_proxy_estimate: { basis: 'bcbstx_uhc_choice_plus_fee_proxy', status: 'provisional',
    reference_total_cents: 19429, copay_cents: 0, deductible_cents: 19429,
    coinsurance_percent: '20', coinsurance_cents: 0, oop_reduction_cents: 0,
    patient_share_cents: 19429, oop_remaining_cents: 455369,
    deductible_source: 'Payer-reported individual remaining deductible; CPT applicability assumed',
    lines: codes.map(([, code, cents]) => ({ code, units: 1, reference_cents: cents,
      coinsurance_base_cents: 0, coinsurance_cents: 0 })), message: 'Provisional BCBSTX fee reference.' },
  requirement_evidence: { procedures },
  collection_guidance: { amount_cents: null, basis: 'missing_collection_inputs' },
}
const model = { cash: false, calculating: false, previousEstimate: false,
  supportedAmount: false, networkLabel: 'In network', estimatedShare: null,
  amountText: '$194.29', provisionalAmount: 19429 }
const html = renderToStaticMarkup(React.createElement(CommercialCostBreakdown, { model, result }))
for (const expected of ['CPT 95908', 'CPT 95886', '$105.11', '$89.18', '$194.29',
  'CPT 95908: not required; CPT 95886: not required',
  'Deductible applied to this scenario', '20% · $0.00']) {
  assert.ok(html.includes(expected), `Missing ${expected}`)
}
assert.match(html, /Amount to collect at visit|Planned amount to collect at visit/)
assert.doesNotMatch(html, /Review selected CPT, benefits and clinic rate before collecting/)
console.log('Choice Plus EMG table rendered the two CPTs, requirements, and $194.29 planning amount')
