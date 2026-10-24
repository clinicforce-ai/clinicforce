import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { default: CommercialCostBreakdown } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialCostBreakdown.tsx',
  )
  const option = (amount_cents, coverage_level, time_period, payer_note = '') => ({
    kind: 'out_of_pocket',
    service_type_code: '30',
    coverage_level,
    time_period,
    network: 'IN_NETWORK',
    plan_index: 0,
    plan_association: 'medical',
    payer_note,
    amount_cents,
  })
  const result = {
    payer: 'Aetna',
    reason: 'Specialist visit and nerve study',
    benefits: {
      active: true,
      service_date_matches: true,
      network_status: 'in_network',
      reported_options: [
        option(400000, 'INDIVIDUAL', 'CALENDAR_YEAR', 'INT MED AND RX'),
        option(349608, 'INDIVIDUAL', 'REMAINING'),
        option(800000, 'FAMILY', 'CALENDAR_YEAR', 'INT MED AND RX'),
        option(704135, 'FAMILY', 'REMAINING'),
      ],
    },
    price_range: {
      mode: 'services',
      complete: false,
      cash_complete: false,
      allowed_min_cents: 19815,
      allowed_max_cents: 19815,
      lines: [
        { id: 'office', code: '99203', units: 1, cash_cents: 15000,
          allowed_cents: 9325, allowed_basis: 'clinic_contract',
          rate_resolution: { status: 'RESOLVED' } },
        { id: 'ncs', code: '95908', units: 1, cash_cents: null,
          allowed_cents: 10490, allowed_basis: 'clinic_contract',
          rate_resolution: { status: 'RESOLVED' } },
      ],
    },
  }
  const model = { cash: false, planName: 'Open Access MC', networkLabel: 'In network',
    policyStatus: 'Active', supportedAmount: false }
  const markup = renderToStaticMarkup(React.createElement(CommercialCostBreakdown, { model, result }))
  for (const value of ['$198.15', '$4,000.00 annual', '$3,496.08 remaining',
    '$8,000.00 annual', '$7,041.35 remaining', '$150.00 new-patient office visit']) {
    assert(markup.includes(value), `Missing ${value}`)
  }
  assert(markup.includes('not used as a verified calculation input'))
  assert(markup.includes('Not established'))
} finally {
  await server.close()
}
