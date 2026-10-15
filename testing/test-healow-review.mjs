// Node 24 strips TypeScript; resolve the frontend's extensionless local imports.
import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
registerHooks({ resolve(specifier, context, nextResolve) {
  try { return nextResolve(specifier, context) } catch (error) {
    if (error.code === 'ERR_MODULE_NOT_FOUND' && specifier.startsWith('.')) return nextResolve(`${specifier}.ts`, context)
    throw error
  }
} })
const { healowDetail, reviewResources } = await import('../frontend/src/features/patient-workspace/healow-review.ts')
const { dateLabel } = await import('../frontend/src/features/patient-workspace/display-text.ts')

process.env.TZ = 'America/Los_Angeles'
assert.equal(dateLabel('1986-11-23'), 'Nov 23, 1986', 'A date-only birthday must not shift to yesterday')
assert.equal(dateLabel('2026-05-16T01:00:00Z'), 'May 15, 2026', 'Timestamps still use the local timezone')
const coverage = healowDetail({ resourceType: 'Coverage', payor: [], status: 'active', subscriberId: 'synthetic' })
assert.equal(coverage.title, 'Insurance coverage')
assert(coverage.details.includes('Insurer name not supplied by the practice'))
const order = healowDetail({ resourceType: 'ServiceRequest', code: { text: 'Vitamin B12' }, status: 'active', authoredOn: '2026-05-16' })
assert.equal(order.title, 'Vitamin B12')
assert(order.details.includes('Order status: active'))
assert(order.details.includes('May 16, 2026'))
const visit = healowDetail({ resourceType: 'Encounter', status: 'cancelled', period: { start: '2026-12-22' } })
assert(visit.details.includes('cancelled'), 'Keep cancelled visits clearly identified')
const condition = healowDetail({ resourceType: 'Condition', code: { text: 'Example condition' }, category: [{ coding: [{ code: 'encounter-diagnosis' }] }] })
assert(condition.details.includes('Recorded at a visit'))
const observation = healowDetail({ resourceType: 'Observation', code: { text: 'Systolic blood pressure' }, valueQuantity: { value: 120, unit: 'mmHg' } })
assert(observation.details.includes('Value: 120 mmHg'))
const documents = [1, 1, 2].map(id => ({ resourceType: 'DocumentReference', id: String(id), type: { text: 'CCD Document' } }))
const record = { bundle: { entry: [...documents, { resourceType: 'Binary', id: 'file' }, { resourceType: 'Provenance', id: 'origin' }, { resourceType: 'Patient', id: 'patient' }].map(resource => ({ resource })) } }
const resources = reviewResources(record)
assert.equal(resources.length, 3, 'Hide technical entries and repeated IDs, but preserve distinct documents')
assert.equal(healowDetail(documents[0]).title, 'Clinical summary')
assert(healowDetail(documents[0]).details.includes('Document details only'))
assert.equal(healowDetail({ resourceType: 'DocumentReference', type: { text: 'CCD Document', coding: [{ display: 'Consultation note' }] } }).title, 'Consultation note', 'Use specific document types rather than identical CCD labels')
console.log('Healow review: dates, coverage, orders, clinical values, visit status, and deduplication passed.')
