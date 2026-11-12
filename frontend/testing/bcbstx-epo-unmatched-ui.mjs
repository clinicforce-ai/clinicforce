import assert from 'node:assert/strict'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { networkRow } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/referenceCoverageRow.ts',
  )
  const result = {
    payer: 'Blue Cross Blue Shield Tx',
    plan_details: { stedi: { names: ['Synthetic EPO medical product'] } },
  }
  const row = networkRow({ networkLabel: 'Patient plan not matched' }, false, result)
  assert.equal(row.value, 'Patient plan not matched')
  assert(row.detail.includes('EPO uses the Blue Choice PPO network'))
  assert(row.detail.includes('Reconcile the subscriber identity'))
  assert.equal(networkRow({ networkLabel: 'In network' }, false, result).detail, undefined)
  assert.equal(
    networkRow({ networkLabel: 'Patient plan not matched' }, false, {
      ...result,
      payer: 'Synthetic other insurer',
    }).detail,
    undefined,
  )
  console.log('BCBSTX EPO review keeps the patient-specific network unresolved')
} finally {
  await server.close()
}
