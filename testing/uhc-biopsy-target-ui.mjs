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

const result = {
  payer: 'UnitedHealthcare',
  collection_guidance: { amount_cents: null, basis: 'missing_collection_inputs' },
  provisional_collection_target: {
    basis: 'bcbstx_uhc_biopsy_deductible_target', status: 'provisional',
    amount_cents: 11660, reference_cents: 11660, oop_remaining_cents: 100000,
    assumption: 'full_deductible_applies',
    message: 'Assumes the full deductible applies; payer order remains unverified.',
  },
}
const model = { cash: false, calculating: false, previousEstimate: false }
const values = { combinedPlan: null, feeProxy: null, aetnaPlanning: null,
  aetnaCopayPlanning: null, officeReferenceCopay: null, humanaReferenceCopay: null,
  partialOfficeCopay: null, pendingAetnaCopay: null, priorDateAetnaCopay: null,
  pendingBluecardCopay: null, cardPlanningCopay: null, cignaCard: null,
  automaticCashFallback: false, cignaFeeProxy: false, proxyFormula: undefined,
  bluecardDeductible: null }
const row = collectionAtVisitRow(result, model, values)
assert.match(row.label, /Provisional collection target/)
assert.match(row.value, /\$116\.60 provisional/)
assert.match(row.detail, /full deductible applies/)
assert.match(row.detail, /payer order remains unverified/)
assert.match(collectionAtVisitRow({ ...result, encounter_summary: {
  payments_verified_cents: 2000 } }, model, values).value, /\$96\.60 provisional/)
const combined = collectionAtVisitRow({ ...result, provisional_collection_target: {
  ...result.provisional_collection_target, pending_codes: ['99213'],
} }, model, values)
assert.match(combined.label, /office share pending/)
assert.match(combined.value, /\$116\.60 provisional biopsy only/)
const priced = collectionAtVisitRow({ ...result, provisional_collection_target: {
  ...result.provisional_collection_target, amount_cents: 19275,
  reference_cents: 19275, included_codes: ['99213', '11104'], pending_codes: [],
} }, model, values)
assert.match(priced.value, /\$192\.75 provisional/)
assert.doesNotMatch(priced.value, /biopsy only/)
assert.doesNotMatch(collectionAtVisitRow(result, { ...model, previousEstimate: true }, values).value, /\$116\.60/)
console.log('UHC biopsy table: $192.75 combined proxy or $116.60 biopsy subtotal, less payments')
