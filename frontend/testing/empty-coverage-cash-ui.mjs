import assert from 'node:assert/strict'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { coveragePolicyStatus } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/coveragePolicyStatus.ts',
  )
  const { referenceCoverageRow } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/referenceCoverageRow.ts',
  )
  const result = {
    payment_mode: 'cash',
    service_date: '2026-11-24',
    insurance_hints: [],
    insurance_diagnosis: { reasonCodes: ['ECW_UPSTREAM_NO_COVERAGE'] },
    identity: { status: 'review' },
  }
  const status = coveragePolicyStatus({ result, tooEarly: false, readOnly: true,
    busy: null, patientBenefitsBound: false })
  assert.equal(status, 'No eCW insurance on file · Self Pay')
  assert.equal(referenceCoverageRow(result, { policyStatus: status }, {}).value,
    'Self Pay · no eCW insurance on file')
  assert.equal(referenceCoverageRow({ ...result, payment_mode: 'insurance' },
    { policyStatus: 'Policy details differ' }, {}).value, 'Payer not recorded')
  console.log('Completed empty eCW coverage displays Self Pay')
} finally {
  await server.close()
}
