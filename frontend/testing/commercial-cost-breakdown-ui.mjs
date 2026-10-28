import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { default: CommercialCostBreakdown } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialCostBreakdown.tsx',
  )
  const { default: ArrivalAmountSummary } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/ArrivalAmountSummary.tsx',
  )
  const { payerPlanDisplay } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/payerPlanDisplay.ts',
  )
  const { aetnaReferenceRequirement } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/aetnaReferenceRequirements.ts',
  )
  const hmoReference = {
    payer: 'Aetna',
    reference_only: true,
    reference_benefits: { active: true, plan_details: { names: ['Synthetic Network-Only HMO'] } },
  }
  assert(aetnaReferenceRequirement(hmoReference, 'referral', '99203')?.startsWith('required'))
  assert(aetnaReferenceRequirement(hmoReference, 'authorization', '64615')?.startsWith('required'))
  assert.equal(
    aetnaReferenceRequirement(hmoReference, 'authorization', '99203'),
    'not required under clinic Aetna rule',
  )
  assert.deepEqual(
    payerPlanDisplay({
      reference_only: true,
      reference_benefits: { active: true, plan_details: { names: ['Synthetic Medicare PPO'] } },
      plan_details: { ecw: { plan: ['Synthetic HMO'] } },
    }),
    {
      planName: 'Synthetic Medicare PPO',
      planSource: 'Stedi payer reference; verify applicability to the appointment date',
    },
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
        {
          id: 'office',
          code: '99203',
          units: 1,
          cash_cents: 15000,
          allowed_cents: 9325,
          allowed_basis: 'clinic_contract',
          rate_resolution: { status: 'RESOLVED' },
        },
        {
          id: 'ncs',
          code: '95908',
          units: 1,
          cash_cents: null,
          allowed_cents: 10490,
          allowed_basis: 'clinic_contract',
          rate_resolution: { status: 'RESOLVED' },
        },
      ],
    },
  }
  const model = {
    cash: false,
    planName: 'Open Access MC',
    networkLabel: 'In network',
    policyStatus: 'Active',
    supportedAmount: false,
  }
  const markup = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, { model, result }),
  )
  for (const value of [
    '$198.15',
    '$4,000.00 annual',
    '$3,496.08 remaining',
    '$8,000.00 annual',
    '$7,041.35 remaining',
    '$150.00 new-patient office visit',
  ]) {
    assert(markup.includes(value), `Missing ${value}`)
  }
  assert(markup.includes('not used as a verified calculation input'))
  assert(markup.includes('Not established'))

  const unpricedAetna = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, {
      model,
      result: {
        payer: 'Aetna',
        reason: 'Memory consultation',
        benefits: {
          active: true, network_status: 'in_network', network_source: 'payer_participation_message',
          service_date_matches: true,
        },
        temporary_rate_reference: {
          source: 'Synthetic Blue Cross schedule',
          message: 'Reference only; not an Aetna allowance.',
          complete: true,
          lines: [{ id: 'office', code: '99204', units: 1, modifiers: [], unit_reference_cents: 15160 }],
        },
        price_range: {
          mode: 'services',
          complete: false,
          lines: [{
            id: 'office', code: '99204', units: 1, modifiers: [], allowed_cents: null,
            benefits: {
              active: true, network_status: 'in_network', selected_benefit_ids: ['synthetic office copay'],
              copay_cents: 6000, coinsurance_percent: '0', deductible_applies: false,
              deductible_remaining_cents: 0, oop_remaining_cents: 639000,
            },
          }],
        },
        collection_guidance: { amount_cents: null, basis: 'missing_collection_inputs' },
      },
    }),
  )
  assert(unpricedAetna.includes('$151.60 BCBS Texas reference'))
  assert(unpricedAetna.includes('$60.00 provisional office copay'))
  assert(unpricedAetna.includes('$60.00 copay + $0.00 deductible + 0%'))
  assert(unpricedAetna.includes('$0.00 now'))

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
              kind: 'coinsurance',
              service_type_code: '98',
              percent: '0',
              coverage_level: 'INDIVIDUAL',
              network: 'IN_AND_OUT_OF_NETWORK',
              places_of_service: ['11'],
            },
            {
              kind: 'copay',
              service_type_code: '98',
              amount_cents: 4000,
              coverage_level: 'INDIVIDUAL',
              network: 'IN_AND_OUT_OF_NETWORK',
              places_of_service: ['11'],
              payer_note: 'Specialist Visit or Evaluation',
            },
            {
              kind: 'copay',
              service_type_code: '98',
              amount_cents: 4000,
              coverage_level: 'INDIVIDUAL',
              network: 'IN_AND_OUT_OF_NETWORK',
              places_of_service: ['02'],
              payer_note: 'Telemedicine Visit by Specialist',
            },
          ],
        },
        price_range: { mode: 'alternatives', lines: [], complete: false },
        collection_guidance: { amount_cents: null },
      },
    }),
  )
  for (const label of [
    'Coverage',
    'Network',
    'CPTs',
    'Blue Cross fee reference',
    'Copay',
    'Individual deductible',
    'Coinsurance',
    'Individual out-of-pocket maximum',
    'Family deductible',
    'Amount to collect at visit',
  ]) {
    assert(aetna.includes(`${label}</th>`), `Missing ${label} in automatic cash fallback`)
  }
  for (const value of [
    '$40.00',
    '$100.00 annual',
    '$0.00 remaining',
    '0% reported',
    '$151.60 per unit',
    '$4,000.00 annual',
    '$2,683.57 remaining',
    'Oct 27, 2026',
    '$40.00 office-only reference',
  ]) {
    assert(aetna.includes(value), `Missing payer-reported value ${value}`)
  }
  assert(
    /Amount to collect at visit<\/th><td><span>\$40\.00 office-only reference<\/span>/.test(aetna),
  )
  assert(aetna.includes('Network</th><td><span>Not determined'))
  assert(!aetna.includes('Estimated patient share</th><td><span>$250.00'))

  const attachedReference = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, {
      model: {
        ...model,
        cash: true,
        cashPrice: '$250.00',
        patientPrice: '$250.00',
        policyStatus: 'Coverage not verified',
        networkLabel: 'Not determined',
      },
      result: {
        payer: 'Aetna',
        service_date: '2026-10-27',
        payment_mode: 'cash',
        cash_fallback: { reason: 'eCW subscriber differs from Aetna.' },
        benefits: { active: null, network_status: 'unknown', reported_options: [] },
        reference_only: true,
        reference_subscriber: {
          first_name: 'Bonnie',
          last_name: 'Mccann',
          member_id: 'SYNTHETIC1234',
        },
        reference_benefits: {
          active: true,
          service_date_matches: false,
          plan_details: { names: ['Synthetic Medicare PPO'] },
          returned_service_dates: { start: '2026-10-26' },
          network_status: 'unknown',
          deductible_annual_cents: 10000,
          deductible_remaining_cents: 0,
          coinsurance_percent: '0',
          oop_annual_cents: 400000,
          oop_remaining_cents: 268357,
          reported_options: [
            {
              kind: 'coinsurance',
              service_type_code: '98',
              percent: '0',
              coverage_level: 'INDIVIDUAL',
              network: 'IN_AND_OUT_OF_NETWORK',
              places_of_service: ['11'],
            },
            {
              kind: 'copay',
              service_type_code: '98',
              amount_cents: 4000,
              coverage_level: 'INDIVIDUAL',
              network: 'IN_AND_OUT_OF_NETWORK',
              places_of_service: ['11'],
              payer_note: 'Specialist Visit or Evaluation',
            },
          ],
        },
        price_range: { mode: 'services', lines: [{ code: '99203', units: 1 }], complete: false },
        collection_guidance: { amount_cents: 25000, basis: 'cash_estimate' },
      },
    }),
  )
  for (const value of [
    '$40.00 specialist office visit',
    '$100.00 annual',
    '$0.00 remaining',
    '0% reported',
    '$4,000.00 annual',
    '$2,683.57 remaining',
    'Oct 27, 2026',
    '$40.00 office-only reference',
    'Stedi subscriber Bonnie Mccann',
    'member ID ending 1234',
    'eCW identity differs',
    'CPT 99203: not required under clinic Aetna rule',
  ]) {
    assert(attachedReference.includes(value), `Missing attached reference value ${value}`)
  }
  assert(!attachedReference.includes('Amount to collect at visit</th><td><span>$250.00'))

  const rejected = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, {
      model: { ...model, cash: true, cashPrice: '$250.00', supportedAmount: true },
      result: {
        payer: 'Aetna',
        payment_mode: 'cash',
        cash_fallback: { reason: 'Eligibility could not be matched.' },
        benefits: { active: null, reported_options: [] },
        collection_guidance: { amount_cents: 25000, basis: 'practice_cash_price' },
      },
    }),
  )
  assert(rejected.includes('Coverage</th>') && rejected.includes('Coinsurance</th>'))
  assert(rejected.includes('Amount to collect at visit</th><td><span>Not established'))
  assert(!rejected.includes('$40.00 specialist office visit'))

  const conflictingCopay = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, {
      model,
      result: {
        ...result,
        price_range: { mode: 'alternatives', lines: [] },
        benefits: {
          active: true,
          reported_options: [4000, 6000].map((amount_cents) => ({
            kind: 'copay',
            service_type_code: '98',
            amount_cents,
            coverage_level: 'INDIVIDUAL',
            network: 'IN_NETWORK',
            places_of_service: ['11'],
            payer_note: 'Specialist Visit or Evaluation',
          })),
        },
      },
    }),
  )
  assert(conflictingCopay.includes('Copay</th><td><span>Not returned for this visit'))

  const cignaDeferred = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, {
      model: { ...model, planName: 'Open Access Plus' },
      result: {
        ...result,
        payer: 'Cigna',
        price_range: { mode: 'services', lines: [{ id: 'ncs', code: '95908', units: 1 }] },
        fee_proxy_estimate: {
          basis: 'bcbstx_cigna_fee_proxy',
          patient_share_cents: 5669,
          reference_total_cents: 28347,
          coinsurance_percent: '20',
          coinsurance_cents: 5669,
          copay_cents: 0,
          deductible_cents: 0,
          oop_reduction_cents: 0,
        },
        collection_guidance: {
          amount_cents: 0,
          basis: 'claim_after_service',
          reason: 'Cigna Cost of Care Estimator required before collecting coinsurance.',
        },
      },
    }),
  )
  assert(cignaDeferred.includes('Amount to collect at visit</th><td><span>$0.00'))
  assert(cignaDeferred.includes('Cigna Cost of Care Estimator required'))

  const cignaPending = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, {
      model: { ...model, planName: 'Open Access Plus' },
      result: {
        ...result,
        payer: 'Cigna',
        price_range: { mode: 'services', lines: [{ id: 'ncs', code: '95908', units: 1 }] },
        fee_proxy_estimate: {
          basis: 'bcbstx_cigna_fee_proxy',
          patient_share_cents: 5669,
          reference_total_cents: 28347,
          coinsurance_percent: '20',
          coinsurance_cents: 5669,
          copay_cents: 0,
          deductible_cents: 0,
          oop_reduction_cents: 0,
        },
        collection_guidance: {
          amount_cents: null,
          basis: 'missing_collection_inputs',
          reason: 'Cigna Explanation of Estimate needed to set a check-in amount.',
        },
      },
    }),
  )
  assert(
    cignaPending.includes(
      'Planned amount to collect at visit</th><td><span>$56.69 planning estimate',
    ),
  )
  assert(cignaPending.includes('Cigna Explanation of Estimate needed'))

  const cignaOfficeScenario = {
    ...result,
    payer: 'Cigna',
    plan: 'Open Access Plus',
    benefits: { ...result.benefits, deductible_remaining_cents: 0, coinsurance_percent: '0' },
    price_range: {
      mode: 'services',
      complete: false,
      lines: [
        { id: 'ncs', code: '95908', units: 1 },
        { id: 'emg', code: '95886', units: 1 },
      ],
    },
    temporary_rate_reference: {
      complete: true,
      lines: [
        { id: 'ncs', code: '95908', units: 1, modifiers: [], unit_reference_cents: 10511 },
        { id: 'emg', code: '95886', units: 1, modifiers: [], unit_reference_cents: 8918 },
      ],
    },
    fee_proxy_estimate: {
      basis: 'bcbstx_cigna_fee_proxy',
      scenario_kind: 'oap_office_copay_general_coinsurance',
      status: 'provisional',
      reference_total_cents: 19429,
      copay_cents: 4000,
      deductible_cents: 0,
      coinsurance_percent: '0',
      coinsurance_cents: 0,
      oop_reduction_cents: 0,
      patient_share_cents: 4000,
      lines: [
        { code: '95908', units: 1, reference_cents: 10511, coinsurance_cents: 0 },
        { code: '95886', units: 1, reference_cents: 8918, coinsurance_cents: 0 },
      ],
      message: 'Conditional planning scenario; obtain Cigna’s Explanation of Estimate.',
    },
    collection_guidance: {
      amount_cents: null,
      basis: 'missing_collection_inputs',
      reason: 'Cigna estimate required before collection.',
    },
  }
  const cignaOfficeTable = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, {
      model: { ...model, planName: 'Open Access Plus' },
      result: cignaOfficeScenario,
    }),
  )
  for (const value of [
    'Visit cost and collection · planning scenario',
    'Formula or source',
    '95908 × 1',
    '95886 × 1',
    'Reference total',
    'Individual deductible applied',
    'Specialist office copay',
    'Diagnostic coinsurance in this scenario',
    '$105.11 BCBS Texas reference',
    '$89.18 BCBS Texas reference',
    '$194.29',
    '$40.00 specialist office visit',
    '$40.00 planning estimate',
    '$105.11 × 0% = $0.00',
    '$89.18 × 0% = $0.00',
  ]) {
    assert(cignaOfficeTable.includes(value), `Missing Cigna scenario value ${value}`)
  }
  const singleCpt = structuredClone(cignaOfficeScenario)
  singleCpt.price_range.lines.pop()
  singleCpt.temporary_rate_reference.lines.pop()
  singleCpt.fee_proxy_estimate.lines.pop()
  singleCpt.fee_proxy_estimate.reference_total_cents = 10511
  const singleCptTable = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, {
      model: { ...model, planName: 'Open Access Plus' },
      result: singleCpt,
    }),
  )
  assert(singleCptTable.includes('95908 × 1'))
  assert(!singleCptTable.includes('95886 × 1'))
  assert(singleCptTable.includes('$105.11'))
  assert(!singleCptTable.includes('$194.29'))
  const cignaHeadline = renderToStaticMarkup(
    React.createElement(ArrivalAmountSummary, {
      model: { ...model, amountText: 'Insured amount unavailable' },
      result: cignaOfficeScenario,
      partialCopay: null,
      visibleAmount: false,
    }),
  )
  assert(cignaHeadline.includes('Cigna planning estimate'))
  assert(cignaHeadline.includes('$40.00'))
  const deferredHeadline = renderToStaticMarkup(
    React.createElement(ArrivalAmountSummary, {
      model: { ...model, amountText: '$0.00', amountLabel: 'Amount to collect at visit' },
      result: {
        ...cignaOfficeScenario,
        collection_guidance: { amount_cents: 0, basis: 'claim_after_service' },
      },
      partialCopay: null,
      visibleAmount: true,
    }),
  )
  assert(!deferredHeadline.includes('Cigna planning estimate'))

  const aetnaDiagnosticResult = {
    payer: 'Aetna', reason: 'Synthetic EMG/NCS evaluation',
    benefits: { active: true, network_status: 'in_network',
      deductible_annual_cents: 0, deductible_remaining_cents: 0,
      oop_annual_cents: 675000, oop_remaining_cents: 639000 },
    price_range: { mode: 'services', complete: false, lines: [
      { id: 'ncs', code: '95908', units: 1, modifiers: [], allowed_cents: null },
      { id: 'emg', code: '95886', units: 1, modifiers: [], allowed_cents: null },
    ] },
    temporary_rate_reference: { source: 'Aetna MEPPO portal table values supplied by clinic',
      complete: true, message: 'Aetna planning reference', lines: [
        { id: 'ncs', code: '95908', units: 1, modifiers: [], unit_reference_cents: 11879 },
        { id: 'emg', code: '95886', units: 1, modifiers: [], unit_reference_cents: 9997 },
      ] },
    aetna_diagnostic_reference: { estimated_allowed_cents: 21876, copay_cents: 10000,
      deductible_cents: 0, coinsurance_cents: 0, coinsurance_percent: '0',
      oop_reduction_cents: 0, oop_remaining_cents: 639000,
      patient_share_cents: 10000, payer_share_cents: 11876,
      benefit_source: { label: '2026 Aetna diagnostic plan document' },
      message: 'Verify fee scope and QMB before collection.' },
    collection_guidance: { amount_cents: null, basis: 'missing_collection_inputs' },
  }
  const aetnaDiagnosticTable = renderToStaticMarkup(React.createElement(
    CommercialCostBreakdown, { model, result: aetnaDiagnosticResult },
  ))
  for (const value of ['$118.79 Aetna fee reference', '$99.97 Aetna fee reference',
    '$218.76 provisional', '$50.00 per diagnostic procedure × 2 = $100.00',
    '$100.00 provisional · Aetna diagnostic estimate', '$118.76', '$0.00 now']) {
    assert(aetnaDiagnosticTable.includes(value), `Missing Aetna diagnostic value ${value}`)
  }
  assert(!aetnaDiagnosticTable.includes('BCBS Texas reference'))
  const aetnaDiagnosticHeadline = renderToStaticMarkup(React.createElement(
    ArrivalAmountSummary, { model: { ...model, amountText: 'Insured amount unavailable' },
      result: aetnaDiagnosticResult, partialCopay: null, visibleAmount: false },
  ))
  assert(aetnaDiagnosticHeadline.includes('Aetna planning estimate'))
  assert(aetnaDiagnosticHeadline.includes('$100.00'))
  assert.equal(aetnaReferenceRequirement({ ...aetnaDiagnosticResult, reference_only: true,
    reference_benefits: { active: true, plan_details: { names: ['Aetna Medicare Signature (PPO)'] } } },
    'authorization', '95908'), null)

  const explicitCash = renderToStaticMarkup(
    React.createElement(CommercialCostBreakdown, {
      model: { ...model, cash: true, cashPrice: '$250.00' },
      result: { ...result, payment_mode: 'cash' },
    }),
  )
  assert(explicitCash.includes('Payment method</th><td><span>Self-pay'))
  assert(!explicitCash.includes('Coinsurance</th>'))
} finally {
  await server.close()
}
