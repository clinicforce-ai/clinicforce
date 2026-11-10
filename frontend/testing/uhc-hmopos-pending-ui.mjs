import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { payerReportedPartialCopay } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/payerReportedPartialCopay.ts',
  )
  const { default: CommercialCostBreakdown } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialCostBreakdown.tsx',
  )
  const { default: ArrivalAmountSummary } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/ArrivalAmountSummary.tsx',
  )
  const line = {
    id: 'office',
    code: '99213',
    units: 1,
    benefits: {
      active: true,
      network_status: 'in_network',
      copay_id: 'synthetic-copay',
      copay_cents: 3500,
      copay_period: 'DAY',
    },
  }
  const result = {
    payer: 'United Healthcare',
    reason: 'Synthetic office visit',
    identity: { match_version: 2, status: 'matched', binding: 'synthetic' },
    plan_details: { stedi: { names: ['HMOPOS-AARP MEDICARE ADVANTAGE FROM UHC'] } },
    benefits: {
      active: true,
      service_date_matches: true,
      network_status: 'in_network',
      network_source: 'clinic_library_contract',
      qmb_status: 'unknown',
    },
    price_range: { mode: 'services', complete: false, lines: [line] },
    collection_guidance: { amount_cents: null, basis: 'missing_collection_inputs' },
    encounter_summary: { payments_verified_cents: 0 },
  }
  const model = {
    cash: false,
    planName: 'Synthetic Medicare Advantage',
    networkLabel: 'In network',
    policyStatus: 'Active',
    supportedAmount: false,
    officeCopayLine: line,
    amountText: 'Insured amount unavailable',
  }
  const copay = payerReportedPartialCopay(result, model)
  assert.equal(copay, 3500)
  const table = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, { model, result }),
  )
  assert(table.includes('verify Medicare protection'))
  assert(table.includes('$35.00 specialist office copay'))
  assert(!table.includes('Pending · $35.00 specialist office copay'))
  const headline = renderToStaticMarkup(
    React.createElement(ArrivalAmountSummary, {
      model,
      result,
      partialCopay: copay,
      visibleAmount: true,
    }),
  )
  assert(headline.includes('verify Medicare protection'))
  assert(headline.includes('$35.00'))
  assert(!headline.includes('Pending · $35.00'))
  const laterLine = { ...line, code: '99214', benefits: { ...line.benefits, copay_cents: 5000 } }
  const laterResult = {
    ...result,
    plan_details: { stedi: { names: ['HMOPOS-UNITEDHEALTHCARE MEDICARE ADVANTAGE'] } },
    price_range: { ...result.price_range, lines: [laterLine] },
    encounter_summary: { payments_verified_cents: 1000 },
  }
  const laterModel = { ...model, officeCopayLine: laterLine }
  const laterCopay = payerReportedPartialCopay(laterResult, laterModel)
  assert.equal(laterCopay, 4000)
  const laterTable = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, { model: laterModel, result: laterResult }),
  )
  assert(laterTable.includes('$40.00 specialist office copay'))
  assert(!laterTable.includes('Pending · $40.00 specialist office copay'))
  assert.equal(
    payerReportedPartialCopay(
      { ...result, benefits: { ...result.benefits, has_secondary: true } },
      model,
    ),
    null,
  )
  assert.equal(
    payerReportedPartialCopay({ ...result, billing_protection: { status: 'qmb' } }, model),
    null,
  )
} finally {
  await server.close()
}
