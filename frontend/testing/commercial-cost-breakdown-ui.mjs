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

  const aetna = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, {
      model: {
        ...model,
        cash: true,
        cashPrice: '$250.00',
        patientPrice: '$250.00',
        policyStatus: 'Coverage dates differ from visit',
        planName: 'ESA PPO',
        networkLabel: 'Not determined',
      },
      result: {
        payer: 'Aetna',
        service_date: '2026-10-27',
        reason: 'Headache consultation',
        payment_mode: 'cash',
        cash_fallback: { reason: 'Payer response requires review.' },
        temporary_rate_reference: {
          source: 'Synthetic Blue Cross schedule',
          message: 'Blue Cross reference only; not an Aetna allowed amount.',
          complete: false,
          lines: [{ code: '99204', unit_reference_cents: 15160 }],
        },
        benefits: {
          active: true,
          service_date_matches: false,
          returned_service_dates: { start: '2026-10-26' },
          network_status: 'unknown',
          deductible_annual_cents: 10000,
          deductible_remaining_cents: 0,
          oop_annual_cents: 400000,
          oop_remaining_cents: 268357,
          coinsurance_percent: '0',
          reported_options: [
            {
              kind: 'coinsurance', service_type_code: '98', percent: '0',
              coverage_level: 'INDIVIDUAL', network: 'IN_AND_OUT_OF_NETWORK',
              places_of_service: ['11'],
            },
            {
              kind: 'copay', service_type_code: '98', amount_cents: 4000,
              coverage_level: 'INDIVIDUAL', network: 'IN_AND_OUT_OF_NETWORK',
              places_of_service: ['11'], payer_note: 'Specialist Visit or Evaluation',
            },
            {
              kind: 'copay', service_type_code: '98', amount_cents: 4000,
              coverage_level: 'INDIVIDUAL', network: 'IN_AND_OUT_OF_NETWORK',
              places_of_service: ['02'], payer_note: 'Telemedicine Visit by Specialist',
            },
          ],
        },
        price_range: { mode: 'alternatives', lines: [], complete: false },
        collection_guidance: { amount_cents: null },
      },
    }),
  )
  for (const label of ['Coverage', 'Network', 'CPTs', 'Blue Cross fee reference', 'Copay', 'Individual deductible', 'Coinsurance',
    'Individual out-of-pocket maximum', 'Family deductible', 'Amount to collect at visit']) {
    assert(aetna.includes(`${label}</th>`), `Missing ${label} in automatic cash fallback`)
  }
  for (const value of ['$40.00', '$100.00 annual', '$0.00 remaining', '0% reported', '$151.60 per unit',
    '$4,000.00 annual', '$2,683.57 remaining', 'Oct 27, 2026', '$40.00 office-only reference']) {
    assert(aetna.includes(value), `Missing payer-reported value ${value}`)
  }
  assert(/Amount to collect at visit<\/th><td><span>\$40\.00 office-only reference<\/span>/.test(aetna))
  assert(aetna.includes('Network</th><td><span>Not determined'))
  assert(!aetna.includes('Estimated patient share</th><td><span>$250.00'))

  const attachedReference = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, {
      model: {
        ...model, cash: true, cashPrice: '$250.00', patientPrice: '$250.00',
        policyStatus: 'Coverage not verified', networkLabel: 'Not determined',
      },
      result: {
        payer: 'Aetna', service_date: '2026-10-27', payment_mode: 'cash',
        cash_fallback: { reason: 'eCW subscriber differs from Aetna.' },
        benefits: { active: null, network_status: 'unknown', reported_options: [] },
        reference_only: true,
        reference_subscriber: {
          first_name: 'Bonnie', last_name: 'Mccann', member_id: 'SYNTHETIC1234',
        },
        reference_benefits: {
          active: true, service_date_matches: false,
          returned_service_dates: { start: '2026-10-26' },
          network_status: 'unknown', deductible_annual_cents: 10000,
          deductible_remaining_cents: 0, coinsurance_percent: '0',
          oop_annual_cents: 400000, oop_remaining_cents: 268357,
          reported_options: [
            {
              kind: 'coinsurance', service_type_code: '98', percent: '0',
              coverage_level: 'INDIVIDUAL', network: 'IN_AND_OUT_OF_NETWORK',
              places_of_service: ['11'],
            },
            {
              kind: 'copay', service_type_code: '98', amount_cents: 4000,
              coverage_level: 'INDIVIDUAL', network: 'IN_AND_OUT_OF_NETWORK',
              places_of_service: ['11'], payer_note: 'Specialist Visit or Evaluation',
            },
          ],
        },
        price_range: { mode: 'services', lines: [{ code: '99203', units: 1 }], complete: false },
        collection_guidance: { amount_cents: 25000, basis: 'cash_estimate' },
      },
    }),
  )
  for (const value of ['$40.00 specialist office visit', '$100.00 annual', '$0.00 remaining',
    '0% reported', '$4,000.00 annual', '$2,683.57 remaining', 'Oct 27, 2026',
    '$40.00 office-only reference', 'Stedi subscriber Bonnie Mccann',
    'member ID ending 1234', 'eCW identity differs']) {
    assert(attachedReference.includes(value), `Missing attached reference value ${value}`)
  }
  assert(!attachedReference.includes('Amount to collect at visit</th><td><span>$250.00'))

  const rejected = renderToStaticMarkup(React.createElement(CommercialCostBreakdown, {
    model: { ...model, cash: true, cashPrice: '$250.00', supportedAmount: true },
    result: {
      payer: 'Aetna', payment_mode: 'cash',
      cash_fallback: { reason: 'Eligibility could not be matched.' },
      benefits: { active: null, reported_options: [] },
      collection_guidance: { amount_cents: 25000, basis: 'practice_cash_price' },
    },
  }))
  assert(rejected.includes('Coverage</th>') && rejected.includes('Coinsurance</th>'))
  assert(rejected.includes('Amount to collect at visit</th><td><span>Not established'))
  assert(!rejected.includes('$40.00 specialist office visit'))

  const conflictingCopay = renderToStaticMarkup(React.createElement(CommercialCostBreakdown, {
    model,
    result: {
      ...result,
      price_range: { mode: 'alternatives', lines: [] },
      benefits: {
        active: true,
        reported_options: [4000, 6000].map((amount_cents) => ({
          kind: 'copay', service_type_code: '98', amount_cents,
          coverage_level: 'INDIVIDUAL', network: 'IN_NETWORK',
          places_of_service: ['11'], payer_note: 'Specialist Visit or Evaluation',
        })),
      },
    },
  }))
  assert(conflictingCopay.includes('Copay</th><td><span>Not returned for this visit'))

  const explicitCash = renderToStaticMarkup(React.createElement(CommercialCostBreakdown, {
    model: { ...model, cash: true, cashPrice: '$250.00' },
    result: { ...result, payment_mode: 'cash' },
  }))
  assert(explicitCash.includes('Payment method</th><td><span>Self-pay'))
  assert(!explicitCash.includes('Coinsurance</th>'))
} finally {
  await server.close()
}
