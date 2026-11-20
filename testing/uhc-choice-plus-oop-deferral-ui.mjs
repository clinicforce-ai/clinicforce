import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { rolldown } = require('rolldown')
const bundle = await rolldown({
  input: new URL('../frontend/src/features/commercial-estimate/collectionAtVisitRow.ts', import.meta.url).pathname,
})
const compiled = await bundle.generate({ format: 'esm' })
const { collectionAtVisitRow } = await import(`data:text/javascript;base64,${Buffer.from(compiled.output[0].code).toString('base64')}`)
const reason = 'Collect $0 at check-in; submit the claim to determine any patient balance.'
const row = collectionAtVisitRow({
  collection_guidance: { amount_cents: 0, basis: 'claim_after_service', reason,
    collection_rule: { mode: 'uhc_choice_plus_unassigned_oop_deferral' } },
}, { supportedAmount: true }, {})
assert.equal(row.label, 'Amount to collect at visit')
assert.equal(row.value, '$0.00')
assert.equal(row.detail, reason)
