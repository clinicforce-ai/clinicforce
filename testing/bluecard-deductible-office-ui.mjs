import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { rolldown } = require('rolldown')
const bundle = await rolldown({
  input: new URL('../frontend/src/features/commercial-estimate/collectionAtVisitRow.ts', import.meta.url).pathname,
})
const compiled = await bundle.generate({ format: 'esm' })
const { collectionAtVisitRow } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled.output[0].code).toString('base64')}`
)
const scenarioBundle = await rolldown({
  input: new URL('../frontend/src/features/commercial-estimate/bluecardOfficeDeductibleScenario.ts', import.meta.url).pathname,
})
const scenarioCompiled = await scenarioBundle.generate({ format: 'esm' })
const { bluecardOfficeDeductibleScenario } = await import(
  `data:text/javascript;base64,${Buffer.from(scenarioCompiled.output[0].code).toString('base64')}`
)
const componentBundle = await rolldown({
  input: new URL('../frontend/src/features/commercial-estimate/CommercialCostBreakdown.tsx', import.meta.url).pathname,
  plugins: [{ name: 'ignore-css', load(id) { if (id.endsWith('.css')) return { code: 'export default {}', moduleType: 'js' } } }],
})
const componentCompiled = await componentBundle.generate({ format: 'esm' })
const { default: CommercialCostBreakdown } = await import(
  `data:text/javascript;base64,${Buffer.from(componentCompiled.output[0].code).toString('base64')}`
)
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')

const identity = { status: 'matched', match_version: 2, binding: 'synthetic' }
const result = {
  payer: 'Blue Cross Blue Shield Tx', service_date: '2026-11-06', identity,
  insurance_details: { group_number: '174302MCAF' },
  plan_details: { stedi: { names: ['PPO PRUDENT BUYER INCENTIVE'] } },
  benefits: { active: true, service_date_matches: true, network_status: 'in_network',
    network_source: 'clinic_group_agreement', deductible_remaining_cents: 400000,
    oop_remaining_cents: 500000, reported_options: [{ kind: 'deductible', service_type_code: '30',
      coverage_level: 'INDIVIDUAL', network: 'IN_NETWORK', time_period: 'REMAINING', amount_cents: 400000 }] },
  price_range: { mode: 'services', lines: [{ id: 'office', code: '99203', units: 1,
    allowed_cents: null, benefits: { active: true, network_status: 'in_network',
      copay_cents: null, coinsurance_percent: '20', deductible_applies: null } }] },
  temporary_rate_reference: { complete: true, source: 'NMCT Clinical Library Blue Cross fee schedule',
    lines: [{ id: 'office', code: '99203', units: 1, modifiers: [], unit_reference_cents: 10117,
      source_reference: 'library:synthetic-fee' }] },
  collection_guidance: { amount_cents: null, basis: 'missing_collection_inputs' },
}
const scenario = bluecardOfficeDeductibleScenario(result)
assert.equal(scenario?.patientShare, 10117)
assert.equal(scenario?.coinsurance, 0)
assert.equal(scenario?.collect, 10117)
const model = { cash: false, calculating: false, previousEstimate: false, supportedAmount: false }
const values = { combinedPlan: null, feeProxy: null, aetnaPlanning: null,
  aetnaCopayPlanning: null, officeReferenceCopay: null, humanaReferenceCopay: null,
  partialOfficeCopay: null, pendingAetnaCopay: null, priorDateAetnaCopay: null,
  pendingBluecardCopay: null, cardPlanningCopay: null, cignaCard: null,
  automaticCashFallback: false, cignaFeeProxy: false, proxyFormula: undefined,
  bluecardDeductible: scenario }
const row = collectionAtVisitRow(result, model, values)
assert.equal(row.label, 'Amount to collect at visit')
assert.equal(row.value, '$101.17 estimated')
assert.match(row.detail, /min\(\$101\.17, \$4,000\.00\) = \$101\.17/)
assert.match(row.detail, /20% × \(\$101\.17 − \$101\.17\) = \$0\.00/)
assert.doesNotMatch(row.value + row.detail, /pending confirmation/i)
const table = renderToStaticMarkup(React.createElement(CommercialCostBreakdown, {
  model: { ...model, networkLabel: 'In network', cashPrice: '$250.00' }, result,
}))
assert.match(table, /Amount to collect at visit/)
assert.match(table, /\$101\.17 estimated/)
assert.match(table, /20%.*\$0\.00 in this estimate/)
assert.doesNotMatch(table, /pending confirmation/i)
assert.equal(bluecardOfficeDeductibleScenario({ ...result,
  temporary_rate_reference: { ...result.temporary_rate_reference,
    lines: [{ ...result.temporary_rate_reference.lines[0],
      source_reference: 'clinic_supplied:blue_choice_ppo_fee_export' }] } })?.collect, 10117)
assert.equal(bluecardOfficeDeductibleScenario({ ...result,
  plan_details: { stedi: { names: ['PPO OTHER'] } } }), null)
assert.equal(bluecardOfficeDeductibleScenario({ ...result,
  benefits: { ...result.benefits, has_secondary: true } }), null)
assert.equal(bluecardOfficeDeductibleScenario({ ...result,
  price_range: { mode: 'services', lines: [{ ...result.price_range.lines[0],
    benefits: { ...result.price_range.lines[0].benefits, copay_cents: 3000 } }] } }), null)
assert.equal(bluecardOfficeDeductibleScenario({ ...result,
  temporary_rate_reference: { ...result.temporary_rate_reference,
    lines: [{ ...result.temporary_rate_reference.lines[0], source_reference: 'unverified' }] } }), null)
const paid = bluecardOfficeDeductibleScenario({ ...result,
  encounter_summary: { payments_verified_cents: 2000 } })
assert.equal(paid?.collect, 8117)
console.log('BlueCard deductible office scenario: $101.17 estimate, formula and safety gates passed')
