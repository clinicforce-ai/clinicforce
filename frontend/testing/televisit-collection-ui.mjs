import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { deriveCommercialEstimateView } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/deriveCommercialEstimateView.ts',
  )
  const { default: CommercialCostBreakdown } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialCostBreakdown.tsx',
  )
  const identity = { status: 'matched', match_version: 2, binding: 'synthetic' }
  const benefits = {
    active: true,
    service_date_matches: true,
    network_status: 'in_network',
    network_source: 'clinic_group_agreement',
    identity,
    oop_remaining_cents: 0,
    review_reasons: [],
    reported_options: [],
  }
  const line = {
    id: 'visit',
    code: '99213',
    units: 1,
    modifiers: [],
    allowed_cents: null,
    calculation: null,
    benefits,
  }
  const result = {
    payer: 'Synthetic Blue',
    reason: 'Televisit',
    service_date: '2026-10-29',
    payment_mode: 'insurance',
    identity,
    benefits,
    issues: [],
    codes: [line],
    selected_ids: ['visit'],
    service_selection_status: 'preview',
    service_plan: { method: 'history', recommended_ids: ['visit'] },
    price_range: { mode: 'services', complete: false, lines: [line] },
    collection_guidance: {
      status: 'estimated',
      basis: 'out_of_pocket_limit',
      amount_cents: 0,
      services: [],
      reason: 'Covered care has no remaining in-network out-of-pocket balance.',
    },
    encounter_summary: { collect_today_cents: 0, complete: false, unpriced_codes: ['99213'] },
  }
  const model = deriveCommercialEstimateView({
    result,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: false,
    recovering: false,
  })
  assert.equal(model.amountText, '$0.00')
  const table = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, { model, result }),
  )
  assert(table.includes('Amount to collect at visit</th><td><span>$0.00'))
  assert(table.includes('99213'))
  assert(table.includes('Not fully priced'))

  const orderedBenefits = {
    ...benefits,
    has_secondary: true,
    oop_remaining_cents: 100000,
    primary_secondary_order: {
      status: 'verified',
      primary_payer_id: '84980',
      secondary_payer: 'Medicare',
      evidence_paths: ['plans[0].benefits.otherOrAdditionalPayer[0]'],
    },
  }
  const ordered = {
    ...result,
    payer: 'Synthetic Blue Cross',
    benefits: orderedBenefits,
    recovery: { reason_code: 'multiple_active_policies', active_policy_count: 2 },
    price_range: {
      ...result.price_range,
      lines: [
        {
          ...line,
          benefits: {
            ...orderedBenefits,
            copay_cents: 4000,
            copay_period: 'DAY',
            coinsurance_percent: '0',
          },
        },
      ],
    },
    collection_guidance: {
      ...result.collection_guidance,
      status: 'estimated',
      basis: 'office_copay',
      amount_cents: 4000,
      primary_secondary_copay: true,
    },
    encounter_summary: { collect_today_cents: 4000, complete: false, unpriced_codes: ['99213'] },
  }
  const orderedModel = deriveCommercialEstimateView({
    result: ordered,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: false,
    recovering: false,
  })
  const orderedTable = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, { model: orderedModel, result: ordered }),
  )
  assert(orderedTable.includes('Amount to collect at visit</th><td><span>$40.00'))
  assert(orderedTable.includes('Medicare secondary'))
  assert(orderedTable.includes('Provisional primary office copay'))
  const uncertain = {
    ...ordered,
    collection_guidance: {
      ...ordered.collection_guidance,
      amount_cents: null,
      primary_secondary_copay: false,
    },
    encounter_summary: { ...ordered.encounter_summary, collect_today_cents: null },
  }
  const uncertainModel = deriveCommercialEstimateView({
    result: uncertain,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: false,
    recovering: false,
  })
  assert.equal(uncertainModel.supportedAmount, false)

  const unresolved = {
    ...result,
    price_range: {
      ...result.price_range,
      service_issues: ['Confirm the patient telehealth location.'],
    },
    collection_guidance: {
      ...result.collection_guidance,
      status: 'action_required',
      basis: 'missing_collection_inputs',
      amount_cents: null,
    },
    encounter_summary: { ...result.encounter_summary, collect_today_cents: null },
  }
  const unresolvedModel = deriveCommercialEstimateView({
    result: unresolved,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: false,
    recovering: false,
  })
  assert.notEqual(unresolvedModel.amountText, '$0.00')
  const unresolvedTable = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, { model: unresolvedModel, result: unresolved }),
  )
  assert(unresolvedTable.includes('Amount to collect at visit</th><td><span>Review selected CPT, benefits and clinic rate before collecting'))

  const bluecardBenefits = {
    ...benefits,
    oop_remaining_cents: 500000,
    deductible_annual_cents: 400000,
    deductible_remaining_cents: 400000,
    reported_options: [{
      kind: 'deductible', service_type_code: '30', coverage_level: 'INDIVIDUAL',
      network: 'IN_NETWORK', time_period: 'REMAINING', amount_cents: 400000,
    }],
  }
  const bluecardResult = {
    ...unresolved,
    reason: 'New patient headache visit',
    benefits: bluecardBenefits,
    price_range: { mode: 'services', complete: false, lines: [{
      ...line, code: '99203', benefits: bluecardBenefits,
    }] },
    payment_explanation: {
      reason_code: 'clinic_product_mismatch',
      sentence: 'Link this member plan to the clinic agreement and rerun the estimate.',
    },
  }
  const bluecardModel = deriveCommercialEstimateView({
    result: bluecardResult, appointments: [], selectedVisit: '', busy: null,
    error: '', readOnly: false, recovering: false,
  })
  const bluecardTable = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, { model: bluecardModel, result: bluecardResult }),
  )
  assert(bluecardTable.includes('$4,000.00 annual · $4,000.00 remaining'))
  assert(bluecardTable.includes('plan-wide deductible under STC 30'))
  assert(bluecardTable.includes('Match member plan to clinic agreement'))

  const cignaResult = {
    ...result,
    payer: 'Cigna',
    benefits: { ...benefits, oop_remaining_cents: 800000 },
    card_check: { status: 'matched', office_copay_cents: 6000 },
    collection_guidance: {
      ...result.collection_guidance,
      status: 'estimated',
      basis: 'office_copay',
      amount_cents: 6000,
      payer_reported_office_copay_cents: 6000,
      collection_rule: { mode: 'cigna_oap_televisit_office_copay', reference: 'Synthetic' },
    },
    encounter_summary: { ...result.encounter_summary, collect_today_cents: 6000 },
  }
  const cignaModel = deriveCommercialEstimateView({
    result: cignaResult,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: false,
    recovering: false,
  })
  assert.equal(cignaModel.amountText, '$60.00')
  const cignaTable = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, { model: cignaModel, result: cignaResult }),
  )
  assert(cignaTable.includes('specialist video visit · payer-reported'))
  assert(cignaTable.includes('$60.00 office copay estimate'))
  assert(cignaTable.includes('Amount to collect at visit</th><td><span>$60.00'))
} finally {
  await server.close()
}
