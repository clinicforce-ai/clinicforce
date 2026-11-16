import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { default: ArrivalAmountSummary } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/ArrivalAmountSummary.tsx',
  )
  const { default: CommercialCostBreakdown } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialCostBreakdown.tsx',
  )
  const { deriveCommercialEstimateView } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/deriveCommercialEstimateView.ts',
  )
  const model = {
    cash: false,
    previousEstimate: false,
    amountText: 'Insured amount unavailable',
    networkLabel: 'In network',
    supportedAmount: false,
  }
  const result = {
    payer: 'Humana Health Plan',
    service_date: '2026-11-16',
    payment_mode: 'insurance',
    identity: { status: 'matched' },
    billing_protection: {},
    plan_details: {
      stedi: { names: ['Synthetic Humana Honor'], medicare_ppo_evidence: 'synthetic' },
    },
    benefits: {
      active: true,
      service_date_matches: true,
      network_status: 'in_network',
      network_source: 'clinic_library_contract',
      coverage_status_conflict: false,
    },
    price_range: {
      mode: 'services',
      complete: false,
      lines: [
        {
          id: 'office',
          code: '99203',
          units: 1,
          allowed_cents: 11362,
          allowed_basis: 'payer_fee_schedule',
          rate_resolution: { status: 'RESOLVED' },
          benefits: {
            active: true,
            copay_cents: 4000,
            copay_period: 'VISIT',
            coinsurance_percent: '0',
            deductible_applies: null,
            oop_remaining_cents: 423799,
            source_errors: [],
          },
        },
      ],
    },
    collection_guidance: { amount_cents: null, basis: 'missing_collection_inputs' },
  }
  const headline = (current) =>
    renderToStaticMarkup(
      React.createElement(ArrivalAmountSummary, {
        model,
        result: current,
        partialCopay: null,
        visibleAmount: true,
      }),
    )
  assert.match(headline(result), /Amount approved to collect at visit/)
  assert.match(headline(result), /<strong role="status">\$0\.00<\/strong>/)
  assert.match(headline(result), /Stedi reports a \$40\.00 specialist office copay/)
  assert.doesNotMatch(headline(result), /Pending|Verify|Confirm|please/i)
  const table = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, { model, result }),
  )
  assert.match(table, /Amount approved to collect at visit<\/th><td><span>\$0\.00/)
  assert.match(table, /\$40\.00 payer-reported specialist office copay/)
  assert.doesNotMatch(table, /Pending|Verify QMB|Confirm QMB|please/i)
  const approved = {
    ...result,
    collection_guidance: {
      amount_cents: 4000,
      status: 'estimated',
      basis: 'humana_specialist_copay_advance',
      payer_reported_office_copay_cents: 4000,
    },
  }
  const approvedModel = { ...model, supportedAmount: true, amount: '$40.00' }
  const approvedHeadline = renderToStaticMarkup(
    React.createElement(ArrivalAmountSummary, {
      model: approvedModel,
      result: approved,
      partialCopay: null,
      visibleAmount: true,
    }),
  )
  const approvedTable = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, { model: approvedModel, result: approved }),
  )
  assert.match(approvedHeadline, /Amount approved to collect at visit/)
  assert.match(approvedHeadline, /<strong role="status">\$40\.00<\/strong>/)
  assert.match(approvedTable, /Amount approved to collect at visit<\/th><td><span>\$40\.00/)
  assert.match(approvedTable, /\$40\.00 payer-reported specialist office copay/)
  assert.doesNotMatch(approvedHeadline + approvedTable, /Pending|Verify QMB|Confirm QMB|please/i)
  const missingRate = {
    ...result,
    identity: { status: 'matched', binding: 'synthetic-binding', match_version: 2 },
    plan_details: { stedi: { names: ['Synthetic office plan'] } },
    price_range: {
      ...result.price_range,
      lines: [
        {
          ...result.price_range.lines[0],
          allowed_cents: null,
          rate_resolution: { status: 'RATE_UNAVAILABLE' },
          calculation: null,
          benefits: {
            ...result.price_range.lines[0].benefits,
            copay_cents: 1500,
            oop_remaining_cents: 331000,
          },
        },
      ],
    },
    collection_guidance: {
      amount_cents: 1500,
      status: 'estimated',
      basis: 'office_copay',
      services: [],
      collection_rule: { mode: 'nmct_fixed_office_copay_advance' },
    },
    encounter_summary: { collect_today_cents: 1500 },
  }
  const missingRateModel = deriveCommercialEstimateView({
    result: missingRate,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(missingRateModel.supportedAmount, true)
  const missingRateHeadline = renderToStaticMarkup(
    React.createElement(ArrivalAmountSummary, {
      model: missingRateModel,
      result: missingRate,
      partialCopay: null,
      visibleAmount: true,
    }),
  )
  const missingRateTable = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, { model: missingRateModel, result: missingRate }),
  )
  assert.match(missingRateHeadline, /<strong role="status">\$15\.00<\/strong>/)
  assert.match(missingRateTable, /Amount to collect at visit<\/th><td><span>\$15\.00/)
  const protectedHeadline = renderToStaticMarkup(
    React.createElement(ArrivalAmountSummary, {
      model: approvedModel,
      result: { ...approved, billing_protection: { status: 'qmb' } },
      partialCopay: null,
      visibleAmount: true,
    }),
  )
  assert.doesNotMatch(
    protectedHeadline,
    /Amount approved to collect at visit<\/span><strong role="status">\$40\.00/,
  )
  for (const unsafe of [
    { ...result, identity: { status: 'review' } },
    { ...result, billing_protection: { status: 'qmb' } },
    { ...result, benefits: { ...result.benefits, has_secondary: true } },
    { ...result, benefits: { ...result.benefits, network_conflict: true } },
    {
      ...result,
      price_range: {
        ...result.price_range,
        lines: [{ ...result.price_range.lines[0], rate_resolution: { status: 'MISSING' } }],
      },
    },
  ])
    assert.doesNotMatch(headline(unsafe), /Stedi reports a \$40\.00 specialist office copay/)
  console.log('Humana specialist headline: passed')
} finally {
  await server.close()
}
