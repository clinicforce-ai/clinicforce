import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { rolldown } = require('rolldown')
const bundle = await rolldown({
  input: new URL('../frontend/src/features/commercial-estimate/collectionAtVisitRow.ts', import.meta.url).pathname,
})
const compiled = await bundle.generate({ format: 'esm' })
const { pendingBluecardOfficeCopay, collectionAtVisitRow } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled.output[0].code).toString('base64')}`
)

const identity = { status: 'warning', match_version: 2, binding: 'synthetic-binding',
  reconciliation_rule: 'payer_matched_patient' }
const terms = { active: true, network_status: 'in_network', copay_cents: 6000,
  copay_period: '7', deductible_applies: false, oop_remaining_cents: 443428, identity }
const result = {
  payer: 'BCBSTX', identity,
  benefits: { active: true, network_status: 'in_network', network_source: 'clinic_library_contract' },
  plan_details: { stedi: { names: ['Example PPO ALTNET'], verification_payer: {
    name: 'Example Blue home plan', payer_id: '123', evidence_path: 'plans[0]' } } },
  price_range: { mode: 'services', lines: [{ id: 'office', code: '99204', units: 1, benefits: terms }] },
  collection_guidance: { amount_cents: null, basis: 'missing_collection_inputs' },
}
const model = { cash: false, calculating: false, previousEstimate: false,
  amount: null, supportedAmount: false }
const values = { combinedPlan: null, feeProxy: null, aetnaPlanning: null,
  aetnaCopayPlanning: null, officeReferenceCopay: null, humanaReferenceCopay: null,
  partialOfficeCopay: null, pendingAetnaCopay: null, cardPlanningCopay: null,
  automaticCashFallback: false, cignaFeeProxy: false, proxyFormula: undefined }
const copay = pendingBluecardOfficeCopay(result, model)
assert.equal(copay, 6000)
const row = collectionAtVisitRow(result, model, { ...values, pendingBluecardCopay: copay })
assert.match(row.label, /pending verification/)
assert.match(row.value, /\$60\.00/)
assert.match(row.detail, /home-plan authorization/)
assert.equal(pendingBluecardOfficeCopay({ ...result, identity: { ...identity, status: 'review' } }, model), null)
assert.equal(pendingBluecardOfficeCopay({ ...result, benefits: { ...result.benefits, has_secondary: true } }, model), null)
assert.equal(pendingBluecardOfficeCopay({ ...result, benefits: { ...result.benefits, network_source: null } }, model), null)
assert.equal(pendingBluecardOfficeCopay(result, { ...model, requirementIssue: 'Check authorization' }), null)
assert.equal(pendingBluecardOfficeCopay({ ...result, price_range: { mode: 'services', lines: [
  { id: 'office', code: '99204', units: 1, benefits: { ...terms, network_status: 'out_of_network' } },
] } }, model), null)
assert.equal(pendingBluecardOfficeCopay({ ...result, price_range: { mode: 'services', lines: [
  { id: 'office', code: '99204', units: 1, benefits: terms },
  { id: 'test', code: '95910', units: 1, benefits: terms },
] } }, model), null)
assert.equal(pendingBluecardOfficeCopay({ ...result, encounter_summary: { payments_verified_cents: 2000 } }, model), 4000)
console.log('BlueCard office table: $60 pending; identity, payer-order, network, service, and payment guards passed')
