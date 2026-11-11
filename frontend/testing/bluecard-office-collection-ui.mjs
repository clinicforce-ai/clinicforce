import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { default: ArrivalAmountSummary } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/ArrivalAmountSummary.tsx',
  )
  const { bluecardOfficeDeductibleScenario } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/bluecardOfficeDeductibleScenario.ts',
  )
  const result = {
    payer: 'Blue Cross Blue Shield Tx',
    plan_details: { stedi: { names: ['Preferred Provider Organization'] } },
    identity: { status: 'matched', binding: 'synthetic-current-policy', match_version: 2 },
    benefits: { active: true, network_status: 'unknown', oop_remaining_cents: 429053 },
    collection_guidance: { amount_cents: null, positive_advance_policy_missing: true },
    price_range: { mode: 'services', lines: [{ code: '99203', units: 1, modifiers: [],
      benefits: { active: true, network_status: 'unknown', copay_cents: 6000,
        copay_period: '27', deductible_applies: false, coinsurance_percent: '0',
        oop_remaining_cents: 429053 } }] },
  }
  const model = { cash: false, calculating: false, previousEstimate: false,
    amount: null, requirementIssue: true, networkLabel: 'Contract match unavailable',
    amountText: 'Insured amount unavailable', guidance: result.collection_guidance }
  const html = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
    { result, model, partialCopay: null, visibleAmount: false }))
  assert.match(html, /Pending · \$60\.00/)
  assert.match(html, /Confirm BlueCard provider participation/)
  assert.doesNotMatch(html, /Approve collection policy/)
  const unpriced = { ...result, benefits: { ...result.benefits, network_status: 'in_network', copay_cents: null },
    collection_guidance: { amount_cents: null, policy_setup_required: true },
    price_range: { mode: 'services', lines: [{ ...result.price_range.lines[0],
      benefits: { ...result.price_range.lines[0].benefits, network_status: 'in_network', copay_cents: null },
      rate_resolution: { status: 'RATE_UNAVAILABLE' } }] } }
  const unpricedHtml = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
    { result: unpriced, model: { ...model, networkLabel: 'In network', requirementIssue: false,
      guidance: unpriced.collection_guidance }, partialCopay: null, visibleAmount: false }))
  assert.match(unpricedHtml, /Check CPT prices/)
  assert.doesNotMatch(unpricedHtml, /Approve collection policy/)
  const blocked = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
    { result: { ...result, identity: { status: 'review' } }, model,
      partialCopay: null, visibleAmount: false }))
  assert.match(blocked, /Verify network/)
  const metLimit = { ...result, price_range: { ...result.price_range,
    lines: [{ ...result.price_range.lines[0], benefits: {
      ...result.price_range.lines[0].benefits, oop_remaining_cents: 0,
    } }] } }
  const metHtml = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
    { result: metLimit, model, partialCopay: null, visibleAmount: false }))
  assert.doesNotMatch(metHtml, /Pending · \$60\.00/)
  const familyCase = {
    payer: 'Blue Cross Blue Shield Tx', service_date: '2026-11-19',
    selected_ids: ['synthetic-office'],
    codes: [{ id: 'synthetic-office', code: '99203', units: 1, modifiers: [] }],
    identity: { status: 'matched', binding: 'synthetic-family-binding', match_version: 2 },
    cpt_authorization: { state: 'complete', pos: '11', network: 'in_network',
      patient_binding: 'synthetic-family-binding' },
    benefits: { active: true, service_date_matches: true, network_status: 'in_network',
      network_source: 'clinic_library_contract', oop_remaining_cents: 450000,
      reported_options: [
        { kind: 'deductible', service_type_code: '30', coverage_level: 'FAMILY',
          network: 'IN_NETWORK', plan_association: 'medical', plan_index: 0,
          time_period: 'REMAINING', amount_cents: 210000 },
        { kind: 'coinsurance', service_type_code: '98', coverage_level: 'INDIVIDUAL',
          network: 'IN_NETWORK', plan_association: 'medical', plan_index: 0,
          percent: '20.0', payer_note: 'BENEFITCLASS-SPECIALIST' },
        { kind: 'copay', service_type_code: '98', coverage_level: 'INDIVIDUAL',
          network: 'IN_NETWORK', plan_association: 'medical', plan_index: 0,
          amount_cents: 0, payer_note: '' },
        { kind: 'deductible', service_type_code: '98', coverage_level: 'INDIVIDUAL',
          network: 'IN_NETWORK', amount_cents: 0,
          payer_note: 'BENEFITCLASS-NUTRITIONAL THERAPY DEDUCTIBLE DOES NOT APPLY' },
      ] },
    collection_guidance: { amount_cents: null },
    price_range: { mode: 'services', service_issues: [], lines: [
      { id: 'synthetic-office', code: '99203', units: 1, modifiers: [], allowed_cents: null,
        benefits: { active: true, network_status: 'in_network', copay_cents: 0,
          coinsurance_percent: '20.0', deductible_applies: null } },
    ] },
    temporary_rate_reference: { complete: true, source: 'NMCT Clinical Library Blue Cross fee schedule',
      lines: [{ id: 'synthetic-office', code: '99203', units: 1, modifiers: [],
        unit_reference_cents: 12345, source_reference: 'library:synthetic-fee' }] },
  }
  assert.equal(bluecardOfficeDeductibleScenario(familyCase)?.collect, 12345)
  const familyHtml = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
    { result: familyCase, model: { ...model, guidance: familyCase.collection_guidance },
      partialCopay: null, visibleAmount: false }))
  assert.match(familyHtml, /\$123\.45/)
  assert.match(familyHtml, /provisional target/)
  assert.match(familyHtml, /clinic proxy collection rule before charging/)
  familyCase.benefits.reported_options.push({ kind: 'copay', service_type_code: '98',
    network: 'IN_NETWORK', plan_association: 'medical', plan_index: 0,
    amount_cents: 3000, payer_note: 'BENEFITCLASS-SPECIALIST' })
  assert.equal(bluecardOfficeDeductibleScenario(familyCase), null)
  familyCase.benefits.reported_options.pop()
  familyCase.benefits.reported_options[3].payer_note = 'BENEFITCLASS-SPECIALIST DEDUCTIBLE DOES NOT APPLY'
  assert.equal(bluecardOfficeDeductibleScenario(familyCase), null)
} finally {
  await server.close()
}
