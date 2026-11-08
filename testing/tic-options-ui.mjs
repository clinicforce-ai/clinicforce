import assert from 'node:assert/strict'
import { createRequire } from 'node:module'

const require = createRequire(new URL('../frontend/package.json', import.meta.url))
const { rolldown } = require('rolldown')
const bundle = await rolldown({
  input: new URL('../frontend/src/features/commercial-estimate/ticTreatmentRows.ts', import.meta.url).pathname,
})
const compiled = await bundle.generate({ format: 'esm' })
const { ticTreatmentRows } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled.output[0].code).toString('base64')}`
)

const cptBundle = await rolldown({
  input: new URL('../frontend/src/features/commercial-estimate/commercialCptRows.ts', import.meta.url).pathname,
})
const cptCompiled = await cptBundle.generate({ format: 'esm' })
const { commercialCptRows } = await import(
  `data:text/javascript;base64,${Buffer.from(cptCompiled.output[0].code).toString('base64')}`
)

const rows = ticTreatmentRows({ reason: 'New patient visit for tics' })
assert.deepEqual(rows.map((row) => row.label), [
  'Suggested option · CBIT', 'Suggested option · focal botulinum toxin',
])
assert.match(rows[0].value, /90834.*90836/)
assert.match(rows[1].detail, /64612.*J0585/)
assert.ok(rows.every((row) => /not included|Neither injection nor drug is included/.test(row.detail)))
assert.deepEqual(ticTreatmentRows({ reason: 'Headache evaluation' }), [])
const result = { reason: 'New patient visit for tics', payer: 'BCBSTX',
  service_date: '2026-11-09', service_selection_status: 'preview',
  service_plan: { method: 'llm' }, suggestions: { groups: [] }, available_codes: [] }
const table = commercialCptRows(result, [{ code: '99203', units: 1 }], null)
assert.equal(table.cpt.value, 'CPT 99203 × 1')
assert.deepEqual(table.candidates.map((row) => row.label), rows.map((row) => row.label))
console.log('Tics visit table: two conditional treatment suggestions; no added charge')
