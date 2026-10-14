import assert from 'node:assert/strict'
import { request } from '../frontend/src/services/api/client.ts'

const originalFetch = globalThis.fetch
const originalWindow = globalThis.window
const calls = []
globalThis.window = { setTimeout, clearTimeout }
globalThis.fetch = async (path, init) => {
  calls.push({ path, headers: new Headers(init.headers) })
  return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
}

try {
  await Promise.all([
    request('/api/doctor/insurance/staff-case', { method: 'POST' }, { staffIntakeId: 'intake-a' }),
    request('/api/doctor/insurance/staff-case', { method: 'POST' }, { staffIntakeId: 'intake-b' }),
    request('/api/doctor/insurance/staff-case', { method: 'POST' }),
    request('/api/patient/session', {}, { staffIntakeId: 'intake-a' }),
  ])
  assert.deepEqual(calls.map(({ headers }) => headers.get('X-ClinicForce-Intake')), [
    'intake-a',
    'intake-b',
    null,
    null,
  ])
} finally {
  globalThis.fetch = originalFetch
  globalThis.window = originalWindow
}

console.log('Staff intake request context stays scoped to each insurance request.')
