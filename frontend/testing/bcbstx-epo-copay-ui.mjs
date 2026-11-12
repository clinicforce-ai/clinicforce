import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { deriveCommercialEstimateView } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/deriveCommercialEstimateView.ts',
  )
  const { referenceCoverageRow, networkRow } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/referenceCoverageRow.ts',
  )
  const { collectionAtVisitRow } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/collectionAtVisitRow.ts',
  )
  const { default: ArrivalAmountSummary } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/ArrivalAmountSummary.tsx',
  )
  const result = {
    payer: 'Blue Cross Blue Shield Tx',
    service_date: '2026-11-12',
    reason: 'right-hand tingling',
    payment_mode: 'insurance',
    plan_details: { stedi: { names: ['BCBS EPO, NATIONAL MED/SURG'],
      verification_payer: { name: 'Highmark Pennsylvania', payer_id: '84980', evidence_path: 'synthetic' } } },
    identity: { status: 'warning', match_version: 2, binding: 'synthetic-bound-policy',
      reconciliation_rule: 'payer_matched_patient' },
    benefits: { active: true, network_status: 'in_network', network_source: 'clinic_group_agreement',
      oop_remaining_cents: 448461 },
    network_evidence: { decision: 'in_network_contract_supported',
      product_network_source: 'signed_bluecard_agreement', home_plan: 'Highmark Pennsylvania' },
    collection_guidance: { amount_cents: 7000, status: 'estimated', basis: 'office_copay',
      reason: 'Collect one payer-reported specialist office copay.',
      assumptions: [], patient_actions: [], services: [],
      collection_rule: { mode: 'bluecard_epo_neurology_office_copay' } },
    price_range: { mode: 'services', lines: [{ id: 'synthetic-office', code: '99203', units: 1,
      modifiers: [], benefits: { active: true, network_status: 'in_network', copay_cents: 7000,
        copay_period: 'CONTRACT', coinsurance_percent: '0', deductible_applies: false,
        oop_remaining_cents: 448461 } }] },
    encounter_summary: { collect_today_cents: 7000, payments_verified_cents: 0 },
  }
  const model = deriveCommercialEstimateView({ result, appointments: [], selectedVisit: null,
    busy: null, error: null, readOnly: false, recovering: false })
  assert.equal(model.supportedAmount, true)
  assert.equal(model.amount, '$70.00')
  assert.equal(model.networkLabel, 'In network · clinic agreement')
  assert.match(referenceCoverageRow(result, model, result.benefits).detail, /Highmark Pennsylvania is the BlueCard home plan/)
  assert.match(networkRow(model, false, result).detail, /Signed Texas BlueCard agreement/)
  const row = collectionAtVisitRow(result, model, {
    combinedPlan: null, feeProxy: null, aetnaPlanning: null, aetnaCopayPlanning: null,
    officeReferenceCopay: null, humanaReferenceCopay: null, partialOfficeCopay: null,
    pendingAetnaCopay: null, priorDateAetnaCopay: null, pendingBluecardCopay: null,
    cardPlanningCopay: null, cignaCard: null, automaticCashFallback: false,
    cignaFeeProxy: false, proxyFormula: undefined, bluecardDeductible: null,
  })
  assert.equal(row.label, 'Amount to collect at visit')
  assert.equal(row.value, '$70.00')
  assert.match(row.detail, /One \$70\.00 in-network neurologist office copay/)
  const html = renderToStaticMarkup(React.createElement(ArrivalAmountSummary,
    { result, model, partialCopay: null, visibleAmount: true }))
  assert.match(html, /\$70\.00/)
  assert.match(html, /payer-reported in-network neurologist office copay/)
  assert.doesNotMatch(html, /Pending · \$70\.00/)
  const paid = { ...result, encounter_summary: { collect_today_cents: 4500,
    payments_verified_cents: 2500 }, collection_guidance: {
    ...result.collection_guidance, amount_cents: 4500 } }
  const paidModel = deriveCommercialEstimateView({ result: paid, appointments: [], selectedVisit: null,
    busy: null, error: null, readOnly: false, recovering: false })
  assert.equal(paidModel.amount, '$45.00')
  console.log('BlueCard EPO specialist office copay is collectible in the UI and table')
} finally {
  await server.close()
}
