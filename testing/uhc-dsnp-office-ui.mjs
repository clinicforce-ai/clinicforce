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
const { default: ArrivalAmountSummary } = await load('../frontend/src/features/commercial-estimate/ArrivalAmountSummary.tsx')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')

const proxy = {
  basis: 'bcbstx_uhc_dsnp_office_proxy', status: 'provisional', scenario_cpt: '99203',
  cpt_selected: false, reference_total_cents: 8807, coinsurance_percent: '20',
  copay_cents: 0, deductible_cents: 0, coinsurance_cents: 1761,
  oop_reduction_cents: 0, oop_remaining_cents: 890071, patient_share_cents: 1761,
  lines: [{ code: '99203', units: 1, reference_cents: 8807,
    coinsurance_base_cents: 8807, coinsurance_cents: 1761 }],
  rate_source: 'Published BCBSTX 2026 office fee schedule',
  deductible_source: 'Stedi reported $0 remaining individual deductible',
  message: 'Pre-Medicaid planning share. Do not collect this share at check-in.',
}
const result = {
  payer: 'UnitedHealthcare', service_date: '2026-11-09', reason: 'Bells Palsy',
  identity: { status: 'matched', match_version: 2, binding: 'synthetic' },
  visit_type: 'new', available_codes: [{ id: 'office', code: '99203', source: 'conditional office E/M alternative' }],
  plan_details: { stedi: { names: ['UnitedHealthcare Dual Complete TX-S003 (HMO-POS D-SNP)'] } },
  benefits: { active: true, service_date_matches: true, network_status: 'unknown',
    resolved_service_type: '98', copay_cents: 0, deductible_remaining_cents: 0,
    coinsurance_percent: '20', oop_remaining_cents: 890071 },
  codes: [], price_range: { mode: 'alternatives', lines: [] },
  temporary_rate_reference: { complete: true, source: proxy.rate_source,
    message: 'BCBS Texas reference, not a UHC allowance.', lines: [{ id: 'office', code: '99203',
      units: 1, unit_reference_cents: 8807 }] },
  fee_proxy_estimate: proxy,
  collection_guidance: { amount_cents: null, basis: 'missing_collection_inputs' },
}
const model = { cash: false, previousEstimate: false, calculating: false,
  supportedAmount: false, networkLabel: 'Not determined', amountText: 'Insured amount unavailable' }
const table = renderToStaticMarkup(React.createElement(CommercialCostBreakdown, { model, result }))
for (const expected of ['ICD-10-CM G51.0', 'provisional CPT 99203', 'Office benefit in/out of network', '$88.07',
  '20% · $17.61', 'Pre-Medicaid planning share', '$0.00 at check-in',
  '$17.61 pre-Medicaid planning share', '$0.00 copay + $0.00 deductible']) {
  assert.ok(table.includes(expected), `Missing ${expected}`)
}
assert.doesNotMatch(table, /UnitedHealthcare allowed amount: \$88\.07/)
const headline = renderToStaticMarkup(React.createElement(ArrivalAmountSummary, {
  model, result, partialCopay: null, visibleAmount: false,
}))
assert.match(headline, /\$0\.00 at check-in/)
assert.match(headline, /\$17\.61 provisional pre-Medicaid share/)
console.log('UHC D-SNP office table and arrival summary show $17.61 planning, $0 check-in')
