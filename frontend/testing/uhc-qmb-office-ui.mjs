import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { uhcQmbOfficeRows } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/uhcQmbOfficeTable.ts',
  )
  const { default: ArrivalAmountSummary } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/ArrivalAmountSummary.tsx',
  )
  const result = {
    payer: 'United Healthcare',
    reason: 'Synthetic specialist follow-up',
    benefits: { active: true, coinsurance_percent: '20' },
    price_range: { mode: 'services', lines: [{ id: 'office', code: '99213', units: 1,
      allowed_cents: null, benefits: { active: true, copay_cents: 0, coinsurance_percent: '20' } }] },
    collection_guidance: { amount_cents: 0, status: 'zero_advance',
      basis: 'published_uhc_tx_qmb_office', reason: 'Matched QMB office benefit.' },
  }
  const labels = [
    'Coverage', 'Member and group', 'Network', 'Reason for visit', 'LLM-proposed CPTs',
    'Synthetic specialist follow-up · CPT 99213 × 1', 'Total payer allowed', 'Copay',
    'Individual deductible', 'Family deductible', 'Coinsurance',
    'Individual out-of-pocket maximum', 'Family out-of-pocket maximum',
    'Referral', 'Prior authorization', 'Estimated patient share', 'Amount to collect at visit',
  ]
  const rows = labels.map((label) => ({ label, value: label === 'Individual deductible'
    ? '$283.00 annual · $0.00 remaining' : label === 'Individual out-of-pocket maximum'
      ? '$9,250.00 annual · $0.00 remaining' : 'Original payer row' }))
  const table = uhcQmbOfficeRows(result, rows)
  assert.deepEqual(table.map((row) => row.label), labels)
  assert.equal(table.at(-1).value, '$0.00')
  assert.equal(table.find((row) => row.label === 'Coinsurance').value,
    '20% reported; $0.00 collectible for covered QMB care')
  assert.equal(table.find((row) => row.label === 'Individual deductible').value,
    '$283.00 annual · $0.00 remaining')
  assert.equal(table.find((row) => row.label === 'Individual out-of-pocket maximum').value,
    '$9,250.00 annual · $0.00 remaining')
  assert.equal(uhcQmbOfficeRows({ ...result, collection_guidance: { basis: 'missing_collection_inputs' } }, rows), null)
  const headline = renderToStaticMarkup(React.createElement(ArrivalAmountSummary, {
    model: { cash: false, previousEstimate: false, calculating: false, supportedAmount: false },
    result, partialCopay: null, visibleAmount: true,
  }))
  assert(headline.includes('Amount to collect at visit'))
  assert(headline.includes('$0.00'))
  assert(!headline.includes('Verify network'))
} finally {
  await server.close()
}
