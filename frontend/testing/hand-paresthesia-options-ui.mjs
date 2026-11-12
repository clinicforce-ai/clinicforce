import assert from 'node:assert/strict'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { suggestedDiagnosisRows } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/suggestedDiagnosisRows.ts',
  )
  const rows = suggestedDiagnosisRows('paresthesias in r hand')
  assert.equal(rows.length, 3)
  assert.equal(rows[0].value, 'ICD-10-CM R20.2 · Paresthesia of skin')
  assert.equal(rows[1].value, 'CPT 95907–95913 · nerve conduction studies')
  assert.equal(rows[2].value, 'CPT 95885 or 95886 · needle EMG')
  assert(rows.slice(1).every((row) => row.detail.includes('not included in the visit estimate')))
  assert.deepEqual(suggestedDiagnosisRows('routine office visit'), [])
  console.log('Hand paresthesia options: conditional CPTs are informational only')
} finally {
  await server.close()
}
