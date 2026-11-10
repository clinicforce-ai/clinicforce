import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { rolldown } = require('rolldown')
async function load(path) {
  const bundle = await rolldown({ input: new URL(path, import.meta.url).pathname })
  const compiled = await bundle.generate({ format: 'esm' })
  return import(`data:text/javascript;base64,${Buffer.from(compiled.output[0].code).toString('base64')}`)
}
const { aetnaOfficePlanningCopay } = await load('../frontend/src/features/commercial-estimate/payerReportedPartialCopay.ts')
const { collectionAtVisitRow } = await load('../frontend/src/features/commercial-estimate/collectionAtVisitRow.ts')
const { commercialOopRows } = await load('../frontend/src/features/commercial-estimate/commercialOopRows.ts')

const terms = { active: true, network_status: 'in_network', service_date_matches: true,
  selected_benefit_ids: ['synthetic-office-copay'], copay_cents: 4000, coinsurance_percent: '0',
  deductible_applies: false, oop_remaining_cents: 0, separate_copay_limit: true,
  copay_remaining_cents: 90000 }
const result = { payer: 'Aetna', service_date: '2026-11-06',
  identity: { status: 'matched', binding: 'synthetic-current-binding', match_version: 2 },
  benefits: { active: true, network_status: 'in_network', network_source: 'payer_participation_message',
    service_date_matches: true, oop_remaining_cents: 0 },
  price_range: { complete: false, mode: 'services', lines: [
    { id: 'office', code: '99213', units: 1, benefits: terms },
  ] },
  collection_guidance: { amount_cents: null, basis: 'missing_collection_inputs',
    positive_advance_policy_missing: true, reason: 'The payer route needs an approved advance policy.' } }
const model = { cash: false, calculating: false, previousEstimate: false, supportedAmount: false }
const planning = aetnaOfficePlanningCopay(result, model, result.price_range.lines)
assert.equal(planning, 4000)
assert.equal(aetnaOfficePlanningCopay({ ...result, price_range: { ...result.price_range,
  lines: [{ ...result.price_range.lines[0], benefits: { ...terms, copay_remaining_cents: 1000 } }] } },
model, [{ ...result.price_range.lines[0], benefits: { ...terms, copay_remaining_cents: 1000 } }]), 1000)
assert.equal(aetnaOfficePlanningCopay({ ...result, price_range: { ...result.price_range,
  lines: [{ ...result.price_range.lines[0], benefits: { ...terms, copay_remaining_cents: null } }] } },
model, [{ ...result.price_range.lines[0], benefits: { ...terms, copay_remaining_cents: null } }]), null)
assert.equal(aetnaOfficePlanningCopay({ ...result, identity: { status: 'review' } },
model, result.price_range.lines), null)
assert.equal(aetnaOfficePlanningCopay(result, model, [
  { ...result.price_range.lines[0], benefits: { ...terms, separate_copay_limit: false } },
]), null)

const row = collectionAtVisitRow(result, model, { combinedPlan: null, feeProxy: null,
  aetnaPlanning: null, aetnaCopayPlanning: planning, officeReferenceCopay: null,
  humanaReferenceCopay: null, partialOfficeCopay: null, pendingAetnaCopay: null,
  cardPlanningCopay: null, automaticCashFallback: false, cignaFeeProxy: false,
  proxyFormula: undefined })
assert.match(row.label, /pending/i)
assert.match(row.value, /\$40\.00/)
assert.match(row.detail, /\$900\.00/)
assert.match(row.detail, /policy/i)
const paidRow = collectionAtVisitRow({ ...result, encounter_summary: { payments_verified_cents: 1000 } },
  model, { combinedPlan: null, feeProxy: null, aetnaPlanning: null, aetnaCopayPlanning: planning,
    officeReferenceCopay: null, humanaReferenceCopay: null, partialOfficeCopay: null,
    pendingAetnaCopay: null, cardPlanningCopay: null, automaticCashFallback: false,
    cignaFeeProxy: false, proxyFormula: undefined })
assert.match(paidRow.value, /\$30\.00/)

const oopRows = commercialOopRows(result, result, result.benefits, false, false, null)
assert.match(oopRows.find((entry) => entry.label === 'Individual out-of-pocket maximum').detail, /separate copay/i)
assert.match(oopRows.find((entry) => entry.label === 'Separate office copay maximum').value, /\$900\.00/)
console.log('Aetna separate copay: $40 pending collection; $0 general OOP does not erase the separate cap')
