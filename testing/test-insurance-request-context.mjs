import assert from 'node:assert/strict'
import { request } from '../frontend/src/services/api/client.ts'

const originalFetch = globalThis.fetch
const originalWindow = globalThis.window
const calls = []
const delays = []
globalThis.window = {
  setTimeout: (callback, delay) => {
    delays.push(delay)
    return setTimeout(callback, delay)
  },
  clearTimeout,
}
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
  await request('/api/doctor/insurance/commercial/appointments/previsit',
    { method: 'POST' }, { staffIntakeId: 'intake-a', timeoutMs: 130_000 })
  assert.equal(delays.at(-1), 130_000)
  assert.equal(calls.at(-1).headers.get('X-ClinicForce-Intake'), 'intake-a')
} finally {
  globalThis.fetch = originalFetch
  globalThis.window = originalWindow
}

console.log('Staff intake request context stays scoped to each insurance request.')
