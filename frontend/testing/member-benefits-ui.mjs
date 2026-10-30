import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { ApplicableBenefits, RequirementCard } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/EstimatePanels.tsx',
  )
  const base = {
    payment_mode: 'insurance',
    benefit_source: 'Stedi',
    codes: [{ code: '99203' }],
    benefits: { active: true, network_status: 'unknown', reported_options: [] },
    price_range: { mode: 'services', lines: [{ id: 'office', benefits: { oop_id: 'payer-row' } }] },
    collection_guidance: {
      services: [
        {
          id: 'office',
          code: '99203',
          benefit: 'Office visit',
          benefit_source: 'Stedi',
          deductible_remaining_cents: 220000,
          oop_remaining_cents: 210000,
        },
      ],
    },
  }
  const render = (identity, recovery = {}) =>
    renderToStaticMarkup(
      React.createElement(ApplicableBenefits, { result: { ...base, identity, recovery } }),
    )
  const currentIdentity = {
    status: 'matched',
    binding: 'synthetic-current-binding',
    match_version: 2,
  }

  const mismatched = render({ status: 'review' }, { reason_code: 'no_coverage_found' })
  assert(mismatched.includes('Payer benefit evidence'))
  assert(mismatched.includes('Payer benefit rows were returned'))
  assert(!mismatched.includes('$2,200.00') && !mismatched.includes('$2,100.00'))

  const unordered = render(currentIdentity, { reason_code: 'multiple_active_policies' })
  assert(!unordered.includes('$2,200.00') && !unordered.includes('$2,100.00'))

  const matched = render(currentIdentity)
  assert(matched.includes('Benefits used for this visit'))
  assert(matched.includes('$2,200.00') && matched.includes('$2,100.00'))
  const zeroOopWithUnknownNetwork = renderToStaticMarkup(
    React.createElement(ApplicableBenefits, {
      result: {
        ...base,
        identity: currentIdentity,
        collection_guidance: {
          services: [{ ...base.collection_guidance.services[0], oop_remaining_cents: 0 }],
        },
      },
    }),
  )
  assert(zeroOopWithUnknownNetwork.includes('payer reports $0 remaining'))
  assert(!zeroOopWithUnknownNetwork.includes('estimated cost for covered in-network care to $0'))
  const historical = renderToStaticMarkup(
    React.createElement(ApplicableBenefits, {
      result: {
        ...base,
        identity: currentIdentity,
        benefits: {
          ...base.benefits,
          historical_only: true,
          source_errors: [{ kind: 'saved_raw_unavailable' }],
        },
      },
    }),
  )
  assert(historical.includes('Previously saved payer benefit evidence'))
  assert(historical.includes('Rerun the payer check before collection'))
  assert(historical.includes('$2,200.00') && historical.includes('$2,100.00'))
  assert(!historical.includes('Benefits used for this visit'))
  assert(!render({ status: 'matched' }).includes('$2,200.00'))
  const legacyMatch = render({ status: 'matched', binding: 'legacy-binding' })
  assert(!legacyMatch.includes('$2,200.00') && !legacyMatch.includes('$2,100.00'))
  assert(
    render({ status: 'matched', binding: 'current-binding', match_version: 2 }).includes(
      '$2,200.00',
    ),
  )
  const perCptBenefits = {
    ...base,
    identity: currentIdentity,
    price_range: {
      mode: 'alternatives',
      lines: [
        { id: 'low', code: '99202', benefits: { copay_id: 'payer-row-low' } },
        { id: 'high', code: '99203', benefits: { copay_id: 'payer-row-high' } },
      ],
    },
    collection_guidance: {
      services: [
        {
          id: 'low',
          code: '99202',
          benefit: 'Office visit',
          copay_cents: 2000,
          selected_benefit_ids: ['payer-row-low'],
        },
        {
          id: 'high',
          code: '99203',
          benefit: 'Office visit',
          copay_cents: 3000,
          selected_benefit_ids: ['payer-row-high'],
        },
      ],
    },
  }
  const perCptView = renderToStaticMarkup(
    React.createElement(ApplicableBenefits, {
      result: perCptBenefits,
    }),
  )
  assert.equal((perCptView.match(/Applicable copay/g) || []).length, 2)
  assert(!perCptView.includes('Shared office-visit terms for CPT alternatives'))
  const ncsCodes = ['95907', '95908', '95909']
  const sharedNcs = renderToStaticMarkup(
    React.createElement(ApplicableBenefits, {
      result: {
        ...base,
        identity: currentIdentity,
        price_range: {
          mode: 'alternatives',
          lines: ncsCodes.map((code) => ({ id: code, code, benefits: { copay_id: 'shared-row' } })),
        },
        collection_guidance: {
          services: ncsCodes.map((code) => ({
            id: code,
            code,
            benefit: 'Nerve study',
            copay_cents: 4000,
            selected_benefit_ids: ['shared-row'],
          })),
        },
      },
    }),
  )
  assert.equal((sharedNcs.match(/Applicable copay/g) || []).length, 1)
  assert(sharedNcs.includes('Shared nerve-study terms for CPT alternatives'))
  const equalAmountsDifferentRows = renderToStaticMarkup(
    React.createElement(ApplicableBenefits, {
      result: {
        ...perCptBenefits,
        collection_guidance: {
          services: [
            perCptBenefits.collection_guidance.services[0],
            { ...perCptBenefits.collection_guidance.services[1], copay_cents: 2000 },
          ],
        },
      },
    }),
  )
  assert.equal((equalAmountsDifferentRows.match(/Applicable copay/g) || []).length, 2)
  const oopConflict = renderToStaticMarkup(
    React.createElement(ApplicableBenefits, {
      result: {
        ...base,
        identity: currentIdentity,
        benefits: {
          ...base.benefits,
          oop_annual_conflict_ids: ['payer-row-1', 'payer-row-2'],
          oop_annual_cents: null,
        },
      },
    }),
  )
  assert(oopConflict.includes('conflicting annual individual out-of-pocket maximum'))
  assert(oopConflict.includes('payer-row-1') && oopConflict.includes('payer-row-2'))
  assert(!oopConflict.includes('for this plan and network tier'))
  const scopedOopConflict = renderToStaticMarkup(
    React.createElement(ApplicableBenefits, {
      result: {
        ...base,
        identity: currentIdentity,
        benefits: {
          ...base.benefits,
          oop_annual_conflict_ids: ['payer-row-1', 'payer-row-2'],
          oop_remaining_conflict_ids: ['payer-row-3', 'payer-row-4'],
          review_reasons: [
            'The payer returned a conflicting annual individual out-of-pocket maximum across payer plans, benefit network tiers. Match the applicable plan, service scope, network tier, coverage level and effective dates with the payer.',
            'The payer returned conflicting remaining individual out-of-pocket balances across effective periods. Match the applicable plan, service scope, network tier, coverage level and effective dates with the payer.',
          ],
        },
      },
    }),
  )
  assert(scopedOopConflict.includes('across payer plans, benefit network tiers'))
  assert(scopedOopConflict.includes('across effective periods'))
  assert(!scopedOopConflict.includes('for this plan and network tier'))
  assert(scopedOopConflict.includes('payer-row-4'))
  const wrongMemberConflict = renderToStaticMarkup(
    React.createElement(ApplicableBenefits, {
      result: {
        ...base,
        identity: { status: 'review' },
        benefits: { ...base.benefits, oop_annual_conflict_ids: ['payer-row-1', 'payer-row-2'] },
      },
    }),
  )
  assert(!wrongMemberConflict.includes('payer-row-1'))
  const requirement = (status) =>
    renderToStaticMarkup(
      React.createElement(RequirementCard, {
        title: 'Prior authorization',
        icon: React.createElement('span'),
        needsCodeSelection: true,
        evidence: { status, reason: 'Synthetic payer evidence', sources: [] },
      }),
    )
  assert(requirement('Verification required').includes('Choose CPT level'))
  assert(requirement('Required').includes('<strong>Yes</strong>'))
  assert(requirement('Not required').includes('<strong>No</strong>'))
  const noAuthorization = renderToStaticMarkup(
    React.createElement(RequirementCard, {
      title: 'Prior authorization',
      icon: React.createElement('span'),
      evidence: {
        status: 'Not required',
        approval_status: 'Not verified',
        reason: 'Exact sourced CPT rule.',
        sources: [],
      },
    }),
  )
  assert(noAuthorization.includes('<strong>No</strong>'))
  assert(!noAuthorization.includes('No approval record'))
  const missingRule = renderToStaticMarkup(
    React.createElement(RequirementCard, {
      title: 'Prior authorization',
      icon: React.createElement('span'),
      plannedCodes: ['99203'],
      payerName: 'United Healthcare',
      evidence: { status: 'Verification required', reason: 'No CPT rule returned', sources: [] },
    }),
  )
  assert(missingRule.includes('Ask United Healthcare whether CPT 99203 needs prior authorization'))
  const { RequirementDecision } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/RequirementDecision.tsx',
  )
  const perCptRule = renderToStaticMarkup(
    React.createElement(RequirementDecision, {
      code: '99203',
      kind: 'referral',
      payerName: 'United Healthcare',
    }),
  )
  assert(perCptRule.includes('Ask United Healthcare whether CPT 99203 needs referral'))
  const unselectedCpt = renderToStaticMarkup(
    React.createElement(RequirementDecision, {
      code: '99203',
      kind: 'referral',
      payerName: 'United Healthcare',
      needsCodeSelection: true,
    }),
  )
  assert(
    unselectedCpt.includes('Select CPT level') && !unselectedCpt.includes('Ask United Healthcare'),
  )
  const conditionalRequired = renderToStaticMarkup(
    React.createElement(RequirementDecision, {
      code: '99203',
      kind: 'prior authorization',
      needsCodeSelection: true,
      evidence: {
        status: 'Required',
        approval_status: 'Not verified',
        reason: 'Exact sourced CPT rule.',
        sources: [],
      },
    }),
  )
  const conditionalNotRequired = renderToStaticMarkup(
    React.createElement(RequirementDecision, {
      code: '99202',
      kind: 'referral',
      needsCodeSelection: true,
      evidence: { status: 'Not required', reason: 'Exact sourced CPT rule.', sources: [] },
    }),
  )
  assert(conditionalRequired.includes('Yes if selected'))
  assert(conditionalRequired.includes('Approval needed if selected'))
  assert(!conditionalRequired.includes('Approval not recorded'))
  assert(conditionalNotRequired.includes('No if selected'))
  const noApprovalNeeded = renderToStaticMarkup(
    React.createElement(RequirementDecision, {
      code: '99202',
      kind: 'prior authorization',
      evidence: {
        status: 'Not required',
        approval_status: 'Not verified',
        reason: 'Exact sourced CPT rule.',
        sources: [],
      },
    }),
  )
  assert(noApprovalNeeded.includes('No'))
  assert(!noApprovalNeeded.includes('Approval not recorded'))
  const { ServicePlanner } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/ServicePlanner.tsx',
  )
  const { nerveCodeForStudyCount } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/service-code.ts',
  )
  assert.deepEqual(
    Array.from({ length: 15 }, (_, count) => nerveCodeForStudyCount(count)),
    [
      '',
      '95907',
      '95907',
      '95908',
      '95908',
      '95909',
      '95909',
      '95910',
      '95910',
      '95911',
      '95911',
      '95912',
      '95912',
      '95913',
      '',
    ],
  )
  const testingPlanner = renderToStaticMarkup(
    React.createElement(ServicePlanner, {
      result: {
        payment_mode: 'insurance',
        payer: 'United Healthcare',
        service_date: '2026-10-15',
        visit_type: 'new',
        codes: [{ id: 'test', code: '95910', units: 1 }],
        available_codes: [{ id: 'test', code: '95910', units: 1 }],
        selected_ids: ['test'],
        service_plan: { kind: 'emg_ncs', recommended_ids: [] },
        price_range: { mode: 'alternatives', lines: [] },
      },
      busy: false,
      save: async () => true,
      viewEstimate: () => {},
    }),
  )
  assert(testingPlanner.includes('Ask United Healthcare whether CPT 95910 needs referral'))
  assert(testingPlanner.includes('id="nerve-study-count"'))
  assert(testingPlanner.includes('value="7"'))
  assert(testingPlanner.includes('is-nerve-detail'))
  assert(!testingPlanner.includes('class="procedure-pick"'))
  assert.equal((testingPlanner.match(/class="procedure-choice/g) || []).length, 1)
  assert(!testingPlanner.includes('Select E/M CPT'))
  const pendingNcs = renderToStaticMarkup(
    React.createElement(ServicePlanner, {
      result: {
        payment_mode: 'insurance',
        payer: 'Synthetic payer',
        service_date: '2026-10-22',
        reason: 'EMG Visit',
        codes: [],
        available_codes: [
          { id: 'ncs', code: '95907', units: 1 },
          { id: 'cash:99203', code: '99203', units: 1 },
          { id: 'cash:99203', code: '99203', units: 1 },
        ],
        selected_ids: [],
        provisional_scenario: { kind: 'ncs_study_count', candidate_ids: ['ncs'] },
        price_range: { mode: 'alternatives', lines: [] },
      },
      busy: false,
      save: async () => true,
      viewEstimate: () => {},
    }),
  )
  assert(pendingNcs.includes('aria-pressed="true">Nerve study'))
  assert(pendingNcs.includes('value="0"'))
  assert(!pendingNcs.includes('class="procedure-choice'))
  assert(!pendingNcs.includes('New patient'))
  assert.equal((pendingNcs.match(/CPT 99203/g) || []).length, 0)
  const duplicateSavedCatalog = renderToStaticMarkup(
    React.createElement(ServicePlanner, {
      result: {
        payment_mode: 'insurance',
        service_date: '2026-10-22',
        visit_type: 'new',
        codes: [],
        available_codes: [
          { id: 'cash:99203', code: '99203', units: 1 },
          { id: 'cash:99203', code: '99203', units: 1 },
        ],
        selected_ids: [],
        price_range: { mode: 'alternatives', lines: [] },
      },
      busy: false,
      save: async () => true,
      viewEstimate: () => {},
    }),
  )
  assert.equal((duplicateSavedCatalog.match(/CPT 99203/g) || []).length, 1)

  const { default: VisitServices } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/VisitServices.tsx',
  )
  const alternativeLines = ['99202', '99203'].map((code) => ({
    id: code,
    code,
    units: 1,
    source: 'eCW appointment',
    benefits: { active: true },
    benefit_basis: 'Office visit benefits',
    calculation: null,
  }))
  const categoryServices = alternativeLines.map((line) => ({
    id: line.id,
    code: line.code,
    selected_benefit_ids: ['office-row'],
  }))
  const alternativeServices = renderToStaticMarkup(
    React.createElement(VisitServices, {
      insuredAmountSupported: true,
      result: {
        payment_mode: 'insurance',
        payer: 'United Healthcare',
        benefit_source: 'Stedi',
        price_range: { mode: 'alternatives', lines: alternativeLines },
        collection_guidance: {
          basis: 'office_copay',
          amount_cents: 3000,
          services: categoryServices,
        },
      },
    }),
  )
  assert.equal((alternativeServices.match(/Select one CPT/g) || []).length, 2)
  assert.equal((alternativeServices.match(/Service-category benefit returned/g) || []).length, 2)
  assert(alternativeServices.includes('The office copay shown above applies once'))
  assert(!alternativeServices.includes('Not established for this CPT and visit scope'))
  assert.equal(
    (alternativeServices.match(/Allowed rate is not needed for the office copay advance/g) || [])
      .length,
    1,
  )
  assert(!alternativeServices.includes('$30.00'))
  const selectedService = renderToStaticMarkup(
    React.createElement(VisitServices, {
      insuredAmountSupported: true,
      result: {
        payment_mode: 'insurance',
        payer: 'United Healthcare',
        benefit_source: 'Stedi',
        identity: currentIdentity,
        benefits: { active: true },
        price_range: { mode: 'services', lines: [alternativeLines[0]] },
        collection_guidance: {
          basis: 'office_copay',
          amount_cents: 3000,
          services: [categoryServices[0]],
        },
      },
    }),
  )
  assert(selectedService.includes('$30.00'))
  assert(!selectedService.includes('Select one CPT'))
  assert(selectedService.includes('Allowed rate: Not needed for this office copay advance'))
  const uhcProductAlias = renderToStaticMarkup(
    React.createElement(VisitServices, {
      result: {
        payment_mode: 'insurance',
        payer: 'AARP Regional PPO',
        stedi_payer: { name: 'UnitedHealthcare', id: 'synthetic-uhc-route' },
        identity: currentIdentity,
        benefits: { active: true },
        price_range: { mode: 'services', lines: [alternativeLines[0]] },
        collection_guidance: { amount_cents: null, status: 'action_required', services: [] },
      },
    }),
  )
  assert(uhcProductAlias.includes('Ask UnitedHealthcare whether CPT 99202 needs referral'))
  const additionalService = renderToStaticMarkup(
    React.createElement(VisitServices, {
      insuredAmountSupported: true,
      result: {
        payment_mode: 'insurance',
        payer: 'United Healthcare',
        identity: currentIdentity,
        benefits: { active: true },
        price_range: {
          mode: 'services',
          lines: [
            alternativeLines[0],
            { id: 'testing', code: '95910', units: 1, benefits: { active: true } },
          ],
        },
        collection_guidance: {
          basis: 'office_copay',
          amount_cents: 3000,
          services: [categoryServices[0]],
        },
      },
    }),
  )
  assert.equal(
    (additionalService.match(/Not needed for this office copay advance/g) || []).length,
    1,
  )
  assert.equal(
    (additionalService.match(/Not established for this CPT and visit scope/g) || []).length,
    1,
  )
  assert(
    additionalService.includes(
      'Other service shares follow the processed claim and member protections',
    ),
  )
  assert(!additionalService.includes('billed after the claim'))
  const differentRulesForSameCpt = renderToStaticMarkup(
    React.createElement(VisitServices, {
      result: {
        ...base,
        identity: currentIdentity,
        price_range: {
          mode: 'services',
          lines: [
            {
              id: 'first-service',
              code: '99213',
              source: 'eCW encounter',
              benefits: { active: true },
            },
            {
              id: 'second-service',
              code: '99213',
              source: 'eCW order',
              benefits: { active: true },
            },
          ],
        },
        requirement_evidence: {
          procedures: [
            {
              id: 'first-service',
              code: '99213',
              referral: { status: 'Required', reason: 'First service rule', sources: [] },
              authorization: { status: 'Not required', reason: 'First service rule', sources: [] },
            },
            {
              id: 'second-service',
              code: '99213',
              referral: { status: 'Not required', reason: 'Second service rule', sources: [] },
              authorization: { status: 'Required', reason: 'Second service rule', sources: [] },
            },
          ],
        },
      },
    }),
  )
  const ruleRows = [
    ...differentRulesForSameCpt
      .split('<tbody>')[1]
      .split('</tbody>')[0]
      .matchAll(/<tr[^>]*>(.*?)<\/tr>/g),
  ].map((match) => match[1])
  assert.equal(ruleRows.length, 2)
  assert(ruleRows[0].indexOf('<td>Yes') < ruleRows[0].indexOf('<td>No'))
  assert(ruleRows[1].indexOf('<td>No') < ruleRows[1].indexOf('<td>Yes'))
  const unmatchedLineId = renderToStaticMarkup(
    React.createElement(VisitServices, {
      result: {
        ...base,
        payer: 'United Healthcare',
        identity: currentIdentity,
        price_range: {
          mode: 'services',
          lines: [{ id: 'second-service', code: '99213', benefits: { active: true } }],
        },
        requirement_evidence: {
          procedures: [
            {
              id: 'first-service',
              code: '99213',
              referral: { status: 'Required', reason: 'Other line', sources: [] },
              authorization: { status: 'Required', reason: 'Other line', sources: [] },
            },
          ],
        },
      },
    }),
  )
  assert(unmatchedLineId.includes('Ask United Healthcare whether CPT 99213 needs referral'))
  assert(
    unmatchedLineId.includes('Ask United Healthcare whether CPT 99213 needs prior authorization'),
  )
  const staleService = renderToStaticMarkup(
    React.createElement(VisitServices, {
      result: {
        payment_mode: 'insurance',
        payer: 'United Healthcare',
        benefit_source: 'Stedi',
        benefits: { active: true },
        price_range: { mode: 'services', lines: [alternativeLines[0]] },
        collection_guidance: {
          basis: 'anticipated_services',
          amount_cents: 4800,
          services: [{ ...categoryServices[0], patient_cents: 4800 }],
        },
      },
    }),
  )
  assert(!staleService.includes('$48.00'))
  assert(staleService.includes('Not calculated'))
  assert(staleService.includes('Payer coverage returned. Patient match unresolved'))

  const { default: CoverageRecovery } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CoverageRecovery.tsx',
  )
  const recoveryView = renderToStaticMarkup(
    React.createElement(CoverageRecovery, {
      model: {
        readOnly: true,
        candidateSuggestion: '',
        failed: false,
        busy: null,
        recovering: false,
      },
      result: {
        ...base,
        recovery: {
          state: 'complete',
          candidates: [
            {
              id: 'candidate-1',
              payer: 'United Healthcare',
              member_id: 'SYNTHETIC',
              plans: ['Choice Plus'],
              subscriber: { firstName: 'Sam', lastName: 'Example' },
              verification_status: 'REVIEW_REQUIRED',
              reason_codes: ['PATIENT_NAME_MISMATCH'],
            },
          ],
        },
      },
    }),
  )
  assert(recoveryView.includes('Patient name differs from eCW'))
  assert.equal((recoveryView.match(/Patient name differs from eCW/g) || []).length, 1)
  const verifiedSpouse = renderToStaticMarkup(
    React.createElement(CoverageRecovery, {
      model: { readOnly: true, failed: false, busy: null, recovering: false },
      result: {
        ...base,
        identity: currentIdentity,
        insurance_hints: [
          { payer: 'Synthetic payer', member_id: 'SYNTHETIC', sources: ['eCW Coverage'] },
        ],
        insurance_diagnosis: {
          identityStatus: 'VERIFIED',
          eligibilityStatus: 'VERIFIED',
          pricingStatus: 'NOT_CALCULATED',
          pricingReasons: ['Contract rate missing'],
        },
      },
    }),
  )
  assert(!verifiedSpouse.includes('Recorded insurance clues'))
  assert(!verifiedSpouse.includes('Insurance check details'))
  assert(!recoveryView.includes('Coverage not verified'))

  const { default: CommercialOverview } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialOverview.tsx',
  )
  const { default: PayerReportedPlanFigures } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/PayerReportedPlanFigures.tsx',
  )
  const topBenefitTerms = renderToStaticMarkup(
    React.createElement(PayerReportedPlanFigures, {
      result: {
        benefits: {
          active: true,
          network_status: 'in_network',
          network_source: 'clinic_group_agreement',
          copay_cents: 3000,
        },
        price_range: {
          mode: 'services',
          lines: [
            {
              code: '95908',
              benefits: { coinsurance_percent: '20', deductible_annual_cents: 35000 },
            },
            { code: '99204', benefits: { copay_cents: 3000, deductible_applies: false } },
          ],
        },
      },
    }),
  )
  assert(topBenefitTerms.includes('Coinsurance · CPT 95908'))
  assert(topBenefitTerms.includes('20%'))
  assert(topBenefitTerms.includes('Remaining balance not returned'))
  assert(topBenefitTerms.includes('$350.00 annual'))
  assert(topBenefitTerms.includes('Individual deductible · CPT 99204'))
  assert(topBenefitTerms.includes('Does not apply'))
  assert(!topBenefitTerms.includes('Match this plan and rendering provider'))
  const { default: CommercialCalculationDetail } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialCalculationDetail.tsx',
  )
  const { default: PatientPolicy } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/PatientPolicy.tsx',
  )
  const missingUhcCard = renderToStaticMarkup(
    React.createElement(PatientPolicy, {
      model: {
        intakeId: null,
        readOnly: false,
        patient: null,
        payerSubscriber: null,
        payerPatient: null,
        policyDates: null,
      },
      result: {
        ...base,
        payer: 'UHC Medicare Advantage Regional PPO',
        card_check: {
          status: 'not_found',
          failure_reason: 'ecw_no_matching_card_document',
        },
      },
    }),
  )
  assert(missingUhcCard.includes('UHC Provider Portal access'))
  assert(missingUhcCard.includes('digital ID card'))
  const missingOtherCard = renderToStaticMarkup(
    React.createElement(PatientPolicy, {
      model: {
        intakeId: null,
        readOnly: false,
        patient: null,
        payerSubscriber: null,
        payerPatient: null,
        policyDates: null,
      },
      result: {
        ...base,
        payer: 'Other insurer',
        card_check: {
          status: 'not_found',
          failure_reason: 'ecw_no_matching_card_document',
        },
      },
    }),
  )
  assert(!missingOtherCard.includes('UHC Provider Portal access'))
  const activeWithoutMetadata = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: {
        readOnly: true,
        cash: false,
        amount: null,
        amountText: 'Insured amount unavailable',
        amountLabel: 'Arrival collection',
        guidance: {
          basis: 'missing_collection_inputs',
          amount_cents: null,
          patient_actions: [],
          assumptions: [],
        },
        policyStatus: 'Coverage active',
        networkLabel: 'Network not established',
      },
      result: { ...base, benefit_source: undefined, checked_at: undefined },
    }),
  )
  assert(activeWithoutMetadata.includes('Payer source not recorded'))
  assert(activeWithoutMetadata.includes('Payer check date unknown'))
  assert(!activeWithoutMetadata.includes('Current payer benefits unavailable'))
  const renderMedicareEnrollment = (payer, enrollment) =>
    renderToStaticMarkup(
      React.createElement(CommercialOverview, {
        model: {
          readOnly: true,
          cash: false,
          amount: null,
          amountText: 'Insured amount unavailable',
          amountLabel: 'Arrival collection',
          policyStatus: 'Clinic payer access missing',
          networkLabel: 'Network not determined',
        },
        result: {
          ...base,
          payer,
          enrollment,
          benefits: { active: null, network_status: 'unknown', reported_options: [] },
        },
      }),
    )
  const enrollmentMessage =
    'ClinicForce does not have a matching Medicare eligibility transaction enrollment through Stedi yet.'
  assert(
    renderMedicareEnrollment('Medicare of Texas', {
      route: 'original_medicare_hets',
      status: 'MISSING',
    }).includes(enrollmentMessage),
  )
  assert(
    !renderMedicareEnrollment('Medicare of Texas', {
      route: 'original_medicare_hets',
      status: 'LIVE',
    }).includes(enrollmentMessage),
  )
  assert(
    !renderMedicareEnrollment('Medicare of Texas', {
      route: 'original_medicare_hets',
      status: 'LOOKUP_FAILED',
    }).includes(enrollmentMessage),
  )
  assert(
    !renderMedicareEnrollment('UHC Medicare Advantage of Texas', {
      route: 'payer_specific_eligibility',
      status: 'MISSING',
    }).includes(enrollmentMessage),
  )
  const full = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: {
        readOnly: true,
        cash: false,
        amount: '$50.00',
        amountText: '$50.00',
        amountLabel: 'Collect at arrival',
        amountParts: {
          copay_cents: 3000,
          deductible_cents: 0,
          coinsurance_cents: 2000,
          oop_adjustment_cents: 0,
        },
        summaryExplanation: 'This $50.00 advance includes $30.00 copay and $20.00 coinsurance.',
        guidance: {
          basis: 'anticipated_services',
          amount_cents: 5000,
          patient_actions: [],
          assumptions: [],
        },
        policyStatus: 'Coverage active',
        networkLabel: 'In network',
        planName: 'Choice Plus',
        anticipatedService: 'Office consultation',
      },
      result: {
        ...base,
        identity: currentIdentity,
        network_evidence: { product_source: 'Stedi eligibility plan name' },
        benefits: {
          ...base.benefits,
          network_status: 'in_network',
          network_source: 'clinic_library_contract',
          oop_annual_cents: 900000,
          oop_remaining_cents: 430000,
        },
        payment_explanation: { reason_code: 'insured_services' },
        collection_guidance: { amount_cents: 5000 },
        encounter_summary: {
          components: {
            copay_cents: 3000,
            deductible_cents: 0,
            coinsurance_cents: 2000,
            oop_adjustment_cents: 0,
          },
          coinsurance_base_cents: 10000,
          coinsurance_percent: '20.00',
        },
      },
    }),
  )
  assert(full.includes('Visit cost and collection'))
  assert(full.includes('Saved visit estimate'))
  assert(full.includes('<summary>Why this amount?</summary>'))
  assert(full.includes('Coinsurance'))
  assert(full.includes('Individual out-of-pocket maximum') && full.includes('$9,000.00'))
  assert(full.includes('$4,300.00 remaining'))
  assert(full.includes('Contract plan lookup: Stedi eligibility plan name'))
  const missingAllowance = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: {
        readOnly: false,
        session: { role: 'admin' },
        cash: false,
        amount: null,
        amountText: 'Insured amount unavailable',
        amountLabel: 'Arrival collection',
        summaryExplanation: 'Ask UHC for the selected CPT allowance.',
        guidance: {
          basis: 'missing_collection_inputs',
          amount_cents: null,
          patient_actions: [],
          assumptions: [],
        },
        policyStatus: 'Coverage active',
        networkLabel: 'Out of network',
        planName: 'Synthetic Regional PPO',
        anticipatedService: 'Office consultation',
      },
      result: {
        ...base,
        selected_ids: ['office'],
        codes: [{ id: 'office', code: '99203', units: 1 }],
        benefits: {
          ...base.benefits,
          network_status: 'out_of_network',
          network_source: 'clinic_nonparticipation_rule',
        },
        payment_explanation: { reason_code: 'out_of_network_allowance_missing' },
      },
    }),
  )
  assert(missingAllowance.includes('Record payer allowed amount for selected CPT'))
  const perCptRounding = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: {
        readOnly: true,
        cash: false,
        amount: '$0.02',
        amountText: '$0.02',
        amountParts: {
          copay_cents: 0,
          deductible_cents: 0,
          coinsurance_cents: 2,
          oop_adjustment_cents: 0,
        },
        guidance: {
          basis: 'anticipated_services',
          amount_cents: 2,
          patient_actions: [],
          assumptions: [],
        },
        summaryExplanation: 'Calculated office and testing share.',
      },
      result: {
        ...base,
        payment_explanation: { reason_code: 'insured_services' },
        price_range: { ...base.price_range, complete: true },
        encounter_summary: {
          complete: true,
          components: { coinsurance_cents: 2 },
          coinsurance_base_cents: null,
          coinsurance_percent: '50',
          coinsurance_rounded_per_line: true,
        },
      },
    }),
  )
  assert(
    perCptRounding.includes('50% applied and rounded separately for each CPT after deductible'),
  )
  assert(!perCptRounding.includes('50% of $0.02'))
  const staleFormula = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: {
        readOnly: true,
        cash: false,
        amount: '$30.00',
        amountText: '$30.00',
        amountParts: {
          copay_cents: 0,
          deductible_cents: 0,
          coinsurance_cents: 3000,
          oop_adjustment_cents: 0,
        },
        guidance: {
          basis: 'anticipated_services',
          amount_cents: 3000,
          patient_actions: [],
          assumptions: [],
        },
      },
      result: {
        ...base,
        payment_explanation: { reason_code: 'insured_services' },
        encounter_summary: {
          components: { coinsurance_cents: 3000 },
          coinsurance_base_cents: 10000,
          coinsurance_percent: '20',
        },
      },
    }),
  )
  assert(staleFormula.includes('Coinsurance'))
  assert(!staleFormula.includes('20% of $100.00'))
  const cappedAmount = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: {
        readOnly: true,
        cash: false,
        amount: '$15.00',
        amountText: '$15.00',
        amountParts: {
          copay_cents: 3000,
          deductible_cents: 0,
          coinsurance_cents: 0,
          allowed_cap_adjustment_cents: 1000,
          oop_adjustment_cents: 500,
        },
        guidance: {
          basis: 'anticipated_services',
          amount_cents: 1500,
          patient_actions: [],
          assumptions: [],
        },
      },
      result: {
        ...base,
        identity: currentIdentity,
        payment_explanation: { reason_code: 'insured_services' },
      },
    }),
  )
  assert(cappedAmount.includes('Visit cost and collection'))
  const unreconciledAmount = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: {
        readOnly: true,
        cash: false,
        amount: '$25.00',
        amountText: '$25.00',
        amountParts: {
          copay_cents: 3000,
          deductible_cents: 0,
          coinsurance_cents: 0,
          allowed_cap_adjustment_cents: 1000,
          oop_adjustment_cents: 500,
        },
        guidance: {
          basis: 'anticipated_services',
          amount_cents: 2500,
          patient_actions: [],
          assumptions: [],
        },
      },
      result: {
        ...base,
        identity: currentIdentity,
        payment_explanation: { reason_code: 'insured_services' },
      },
    }),
  )
  assert(unreconciledAmount.includes('Amount to collect at visit'))
  const outOfNetwork = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: {
        readOnly: true,
        cash: false,
        amount: '$20.00',
        amountText: '$20.00',
        networkLabel: 'Out of network',
        amountParts: {
          copay_cents: 0,
          deductible_cents: 0,
          coinsurance_cents: 2000,
          oop_adjustment_cents: 0,
        },
        guidance: {
          basis: 'anticipated_services',
          amount_cents: 2000,
          patient_actions: [],
          assumptions: [],
        },
      },
      result: {
        ...base,
        benefits: { ...base.benefits, network_status: 'out_of_network' },
        price_range: {
          mode: 'services',
          lines: [
            {
              id: 'office',
              code: '99203',
              allowed_basis: 'payer_out_of_network',
              benefits: { active: true },
            },
          ],
        },
        payment_explanation: { reason_code: 'insured_services' },
      },
    }),
  )
  assert(outOfNetwork.includes('Network</th><td><span>Out of network'))
  assert(!outOfNetwork.includes('using in-network contracted CPT prices'))
  const withoutCopay = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: {
        readOnly: true,
        cash: false,
        amount: '$50.00',
        amountText: '$50.00',
        amountLabel: 'Collect at arrival',
        amountParts: {
          copay_cents: 0,
          deductible_cents: 2000,
          coinsurance_cents: 3000,
          oop_adjustment_cents: 0,
        },
        guidance: {
          basis: 'anticipated_services',
          amount_cents: 5000,
          patient_actions: [],
          assumptions: [],
        },
        summaryExplanation: 'This $50.00 advance includes deductible and coinsurance.',
        policyStatus: 'Coverage active',
        networkLabel: 'In network',
        planName: 'Choice Plus',
        anticipatedService: 'Office consultation',
      },
      result: {
        ...base,
        identity: currentIdentity,
        payment_explanation: { reason_code: 'insured_services' },
        encounter_summary: {
          components: { coinsurance_cents: 3000 },
          coinsurance_base_cents: 15000,
          coinsurance_percent: '20.00',
        },
      },
    }),
  )
  assert(withoutCopay.includes('Visit cost and collection'))
  assert(withoutCopay.includes('<summary>Why this amount?</summary>'))
  const officeCopayView = (paid) =>
    renderToStaticMarkup(
      React.createElement(CommercialOverview, {
        model: {
          readOnly: true,
          cash: false,
          amount: '$30.00',
          amountText: '$30.00',
          amountLabel: 'Collect at arrival',
          amountParts: { copay_cents: 5000, deductible_cents: null, coinsurance_cents: null },
          guidance: {
            basis: 'office_copay',
            amount_cents: 3000,
            patient_actions: [],
            assumptions: [],
          },
          summaryExplanation: 'Collect $30.00 for the consultation after the recorded payment.',
          policyStatus: 'Coverage active',
          networkLabel: 'In network',
          anticipatedService: 'Office consultation',
        },
        result: {
          ...base,
          identity: currentIdentity,
          payment_explanation: { reason_code: 'office_copay' },
          encounter_summary: { payments_verified_cents: paid },
        },
      }),
    )
  const reconciledCopay = officeCopayView(2000)
  assert(reconciledCopay.includes('Already paid</th><td><span>−$20.00'))
  assert(officeCopayView(1000).includes('Already paid</th><td><span>−$10.00'))
  for (const change of [
    { network_conflict: true },
    { active: false },
    { service_date_matches: false },
    { coverage_status_conflict: true },
    { review_reasons: ['Provider-network evidence reports nonparticipation.'] },
    { source_errors: [{ path: 'payer.benefitAmount', kind: 'invalid_amount' }] },
  ]) {
    const unsupported = renderToStaticMarkup(
      React.createElement(CommercialOverview, {
        model: {
          readOnly: true,
          cash: false,
          amount: '$30.00',
          amountText: '$30.00',
          amountLabel: 'Recorded recommendation at this visit',
          guidance: { basis: 'office_copay', patient_actions: [], assumptions: [] },
          summaryExplanation: 'Recorded office copay.',
        },
        result: {
          ...base,
          identity: currentIdentity,
          benefits: {
            ...base.benefits,
            active: true,
            network_status: 'in_network',
            network_source: 'clinic_library_contract',
            oop_annual_cents: 900000,
            oop_remaining_cents: 430000,
            ...change,
          },
          encounter_summary: { complete: false },
        },
      }),
    )
    assert(!unsupported.includes('Applicable in-network plan limits'))
  }
  const clinicZero = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: {
        readOnly: true,
        cash: false,
        amount: '$0.00',
        amountText: '$0.00',
        amountLabel: 'Recorded recommendation at this visit',
        guidance: { basis: 'claim_after_service', patient_actions: [], assumptions: [] },
        summaryExplanation: 'Collect $0 today under the clinic policy.',
      },
      result: {
        ...base,
        identity: currentIdentity,
        benefits: {
          ...base.benefits,
          network_status: 'in_network',
          network_source: 'clinic_library_contract',
          oop_annual_cents: 900000,
          oop_remaining_cents: 430000,
        },
        encounter_summary: { complete: false },
      },
    }),
  )
  assert(!clinicZero.includes('Applicable in-network plan limits'))
  const { deriveCommercialEstimateView } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/deriveCommercialEstimateView.ts',
  )
  const savedReference = {
    ...base,
    payer: 'Aetna',
    payment_mode: 'cash',
    cash_fallback: { reason: 'Eligibility needs review' },
    collection_guidance: {
      ...base.collection_guidance,
      patient_actions: [],
      assumptions: [],
      status: 'action_required',
    },
    reference_only: true,
    reference_benefits: {
      active: true,
      service_date_matches: false,
      network_status: 'unknown',
      network_source: null,
    },
    benefits: { active: false, network_status: 'unknown', reported_options: [] },
  }
  const savedReferenceModel = deriveCommercialEstimateView({
    result: savedReference,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(savedReferenceModel.coverageTone, 'is-confirmed')
  assert.equal(savedReferenceModel.policyStatus, 'Aetna coverage active')
  assert.equal(savedReferenceModel.networkLabel, 'Not determined')
  assert.equal(savedReferenceModel.networkTone, 'is-unknown')
  const savedReferenceOverview = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: { ...savedReferenceModel, readOnly: true },
      result: savedReference,
    }),
  )
  assert(savedReferenceOverview.includes('class="is-confirmed"'))
  assert(savedReferenceOverview.includes('Aetna coverage active'))
  assert(savedReferenceOverview.includes('Not determined'))
  assert(savedReferenceOverview.includes('Sources: eCW + Stedi payer reference'))
  assert(!savedReferenceOverview.includes('Sources: eCW · Payer check needed'))
  const unknownReferenceModel = deriveCommercialEstimateView({
    result: {
      ...savedReference,
      reference_benefits: {
        ...savedReference.reference_benefits,
        network_status: 'unknown',
        network_source: null,
      },
    },
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(unknownReferenceModel.networkLabel, 'Not determined')
  assert.equal(unknownReferenceModel.networkTone, 'is-unknown')
  const { default: ArrivalAmountSummary } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/ArrivalAmountSummary.tsx',
  )
  const cashAmountModel = (payer) =>
    deriveCommercialEstimateView({
      result: { ...base, payer, payment_mode: 'cash' },
      appointments: [],
      selectedVisit: '',
      busy: null,
      error: '',
      readOnly: false,
      recovering: false,
    })
  assert.equal(
    cashAmountModel('Medicare of Texas').amountText,
    'Clinicforce is not yet enrolled for medicare of Texas',
  )
  assert(
    renderToStaticMarkup(
      React.createElement(ArrivalAmountSummary, {
        model: cashAmountModel('Medicare of Texas'),
        result: { ...base, payer: 'Medicare of Texas', payment_mode: 'cash' },
        partialCopay: null,
        visibleAmount: false,
      }),
    ).includes(
      'class="amount-pending" role="status">Clinicforce is not yet enrolled for medicare of Texas',
    ),
  )
  assert.equal(cashAmountModel('Other insurer').amountText, 'Confirm clinic price')
  const clinicNonparticipation = deriveCommercialEstimateView({
    result: {
      ...base,
      identity: currentIdentity,
      benefits: {
        ...base.benefits,
        network_status: 'out_of_network',
        network_source: 'clinic_nonparticipation_rule',
      },
      collection_guidance: {
        ...base.collection_guidance,
        amount_cents: null,
        status: 'action_required',
        basis: 'missing_collection_inputs',
      },
    },
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: false,
    recovering: false,
  })
  assert.equal(clinicNonparticipation.networkLabel, 'Out of network')
  assert.equal(clinicNonparticipation.amountText, 'Insured amount unavailable')
  const noncontractedView = deriveCommercialEstimateView({
    result: {
      ...base,
      identity: currentIdentity,
      benefits: {
        ...base.benefits,
        network_status: 'out_of_network',
        network_source: 'clinic_no_insurer_contract',
      },
    },
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: false,
    recovering: false,
  })
  assert.equal(noncontractedView.networkLabel, 'Out of network')
  assert.equal(noncontractedView.networkSource, 'No current insurer contract registered in the NMCT Clinical Library')
  assert.equal(noncontractedView.amountText, 'Insured amount unavailable')
  const scopedOonResult = {
    ...base,
    identity: currentIdentity,
    benefits: {
      ...base.benefits,
      network_status: 'out_of_network',
      network_source: 'clinic_nonparticipation_rule',
    },
    price_range: {
      mode: 'services',
      complete: true,
      patient_min_cents: 4000,
      patient_max_cents: 4000,
      lines: [
        {
          id: 'office',
          code: '99203',
          units: 1,
          benefits: { active: true, network_status: 'out_of_network' },
          allowed_basis: 'payer_out_of_network',
          allowed_cents: 10000,
          rate_resolution: { status: 'RESOLVED' },
          calculation: { patient_cents: 4000 },
        },
      ],
    },
    collection_guidance: {
      amount_cents: 4000,
      status: 'estimated',
      basis: 'anticipated_services',
      services: [],
    },
    encounter_summary: { collect_today_cents: 4000 },
  }
  const scopedOonView = deriveCommercialEstimateView({
    result: scopedOonResult,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: false,
    recovering: false,
  })
  assert.equal(scopedOonView.amountText, '$40.00')
  const inNetworkRateOon = {
    ...scopedOonResult,
    price_range: {
      ...scopedOonResult.price_range,
      lines: [{ ...scopedOonResult.price_range.lines[0], allowed_basis: 'clinic_contract' }],
    },
  }
  const unsafeOonView = deriveCommercialEstimateView({
    result: inNetworkRateOon,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: false,
    recovering: false,
  })
  assert.equal(unsafeOonView.amountText, 'Insured amount unavailable')
  const savedView = deriveCommercialEstimateView({
    result: {
      ...base,
      plan_details: { stedi: { names: [], network_names: [] }, ecw: { plan: [] } },
      collection_guidance: {
        ...base.collection_guidance,
        amount_cents: 5000,
        basis: 'office_copay',
      },
    },
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(savedView.amountLabel, 'Recorded recommendation at this visit')
  const unresolvedResult = {
    ...base,
    identity: { status: 'review' },
    plan_details: { stedi: { names: [], network_names: [] }, ecw: { plan: [] } },
    collection_guidance: {
      ...base.collection_guidance,
      amount_cents: null,
      status: 'action_required',
      basis: 'missing_collection_inputs',
      reason:
        'The payer patient name differs from eCW. Reconcile the name with UHC before collecting.',
      patient_actions: [],
      assumptions: [],
    },
  }
  const unresolvedModel = deriveCommercialEstimateView({
    result: unresolvedResult,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: false,
    recovering: false,
  })
  const unresolvedPage = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: unresolvedModel,
      result: unresolvedResult,
    }),
  )
  assert.equal(unresolvedModel.amountLabel, 'Collection decision needed')
  assert(unresolvedPage.includes('Reconcile the name with UHC before collecting.'))
  assert(!unresolvedPage.includes('bill after the claim'))
  assert(!unresolvedPage.includes('No insurance advance today'))
  const invalidActionAmount = {
    ...unresolvedResult,
    collection_guidance: { ...unresolvedResult.collection_guidance, amount_cents: 5000 },
  }
  const invalidActionModel = deriveCommercialEstimateView({
    result: invalidActionAmount,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: false,
    recovering: false,
  })
  assert.equal(invalidActionModel.amountText, 'Insured amount unavailable')
  const approvedZeroResult = {
    ...unresolvedResult,
    collection_guidance: {
      ...unresolvedResult.collection_guidance,
      amount_cents: 0,
      status: 'zero_advance',
      basis: 'claim_after_service',
      reason: 'Collect $0 today under the clinic deferral policy.',
    },
  }
  const approvedZeroModel = deriveCommercialEstimateView({
    result: approvedZeroResult,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: false,
    recovering: false,
  })
  const approvedZeroPage = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: approvedZeroModel,
      result: approvedZeroResult,
    }),
  )
  assert.equal(approvedZeroModel.amountText, '$0.00')
  assert(approvedZeroPage.includes('No advance today. Submit the insurance claim'))
  const fixedCopayResult = {
    ...base,
    payer: 'United Healthcare',
    identity: currentIdentity,
    benefits: { ...base.benefits, service_date_matches: true, has_secondary: false },
    plan_details: { stedi: { names: ['Choice Plus'], network_names: [] }, ecw: { plan: [] } },
    price_range: {
      mode: 'services',
      complete: false,
      lines: [
        {
          id: 'office',
          code: '99203',
          benefits: { active: true, network_independent: true, copay_cents: 5000 },
          calculation: null,
        },
      ],
    },
    collection_guidance: {
      ...base.collection_guidance,
      status: 'estimate',
      basis: 'office_copay',
      amount_cents: 3000,
      reason: 'Applicable office copay after recorded payment.',
      patient_actions: [],
      assumptions: [],
    },
    encounter_summary: {
      collect_today_cents: 3000,
      payments_verified_cents: 2000,
      components: { copay_cents: 5000, deductible_cents: null, coinsurance_cents: null },
    },
    payment_explanation: {
      reason_code: 'office_copay',
      sentence: 'Collect $30.00 for the consultation after the recorded payment.',
    },
  }
  const fixedCopayModel = deriveCommercialEstimateView({
    result: fixedCopayResult,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(fixedCopayModel.amountText, '$30.00')
  const fixedCopayPage = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: fixedCopayModel,
      result: fixedCopayResult,
    }),
  )
  assert(fixedCopayPage.includes('Copay</th><td><span>CPT 99203: $50.00'))
  assert(fixedCopayPage.includes('Already paid</th><td><span>−$20.00'))
  const fixedCopayCalculation = renderToStaticMarkup(
    React.createElement(CommercialCalculationDetail, {
      model: fixedCopayModel,
      result: fixedCopayResult,
    }),
  )
  assert(fixedCopayCalculation.includes('Known office copay advance'))
  assert(fixedCopayCalculation.includes('$30.00'))
  assert(!fixedCopayCalculation.includes('See collection action above'))
  const amountTextFor = (staleResult) =>
    deriveCommercialEstimateView({
      result: staleResult,
      appointments: [],
      selectedVisit: '',
      busy: null,
      error: '',
      readOnly: true,
      recovering: false,
    }).amountText
  for (const flag of ['coverage_status_conflict', 'coverage_dates_invalid']) {
    assert.equal(
      amountTextFor({
        ...fixedCopayResult,
        benefits: { ...fixedCopayResult.benefits, [flag]: true },
      }),
      'Insured amount unavailable',
    )
  }
  const staleCoverageModel = deriveCommercialEstimateView({
    result: {
      ...fixedCopayResult,
      benefits: { ...fixedCopayResult.benefits, coverage_status_conflict: true },
    },
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert(staleCoverageModel.summaryExplanation.includes('conflicting active and inactive coverage'))
  const staleTotalResult = {
    ...fixedCopayResult,
    benefits: { ...fixedCopayResult.benefits, coverage_status_conflict: true },
    collection_guidance: {
      ...fixedCopayResult.collection_guidance,
      patient_message: 'Collect the old $30.00 estimate.',
      patient_actions: ['Collect the old $30.00 estimate.'],
      assumptions: ['Old $120.00 patient total.'],
    },
    encounter_summary: {
      ...fixedCopayResult.encounter_summary,
      estimated_total_patient_min_cents: 12000,
      estimated_total_patient_max_cents: 12000,
      estimated_later_balance_cents: 9000,
    },
  }
  const staleTotalModel = deriveCommercialEstimateView({
    result: staleTotalResult,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  const staleTotalPage = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: staleTotalModel,
      result: staleTotalResult,
    }),
  )
  assert.equal(staleTotalModel.amountText, 'Insured amount unavailable')
  assert(!staleTotalPage.includes('Patient cost breakdown'))
  assert(!staleTotalPage.includes('$120.00'))
  assert(!staleTotalPage.includes('$90.00'))
  assert(!staleTotalPage.includes('Collect the old $30.00 estimate.'))
  assert(!staleTotalPage.includes('Old $120.00 patient total.'))
  const savedTestingResult = {
    ...fixedCopayResult,
    benefits: {
      ...fixedCopayResult.benefits,
      network_status: 'in_network',
      network_source: 'clinic_library_contract',
    },
    collection_guidance: { ...fixedCopayResult.collection_guidance, basis: 'anticipated_services' },
    emg_option: {
      calculation_version: 2,
      requested: true,
      included: false,
      additional_min_cents: 7000,
      additional_max_cents: 7000,
      total_min_cents: 10000,
      total_max_cents: 10000,
      message: 'Saved testing scenario.',
      scenario_count: 1,
    },
  }
  const testingModelFor = (testingResult) =>
    deriveCommercialEstimateView({
      result: testingResult,
      appointments: [],
      selectedVisit: '',
      busy: null,
      error: '',
      readOnly: true,
      recovering: false,
    })
  assert.equal(testingModelFor(savedTestingResult).emg.total_min_cents, 10000)
  const conflictedTestingModel = testingModelFor({
    ...savedTestingResult,
    benefits: { ...savedTestingResult.benefits, coverage_status_conflict: true },
  })
  assert.equal(conflictedTestingModel.emg.total_min_cents, null)
  assert.equal(conflictedTestingModel.emg.additional_min_cents, null)
  assert(conflictedTestingModel.emg.message.includes('Recalculate the testing scenario'))
  const staleNetworkModel = deriveCommercialEstimateView({
    result: {
      ...fixedCopayResult,
      benefits: { ...fixedCopayResult.benefits, network_conflict: true },
    },
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert(staleNetworkModel.summaryExplanation.includes('Provider participation evidence conflicts'))
  for (const lineChange of [
    { active: false },
    { service_date_matches: false },
    { network_conflict: true },
    { has_secondary: true },
    { historical_only: true },
    { identity: { status: 'review' } },
    { identity: { status: 'matched', binding: 'another-member', match_version: 2 } },
    { network_status: 'out_of_network' },
  ]) {
    const stale = {
      ...fixedCopayResult,
      benefits:
        lineChange.network_status === 'out_of_network'
          ? {
              ...fixedCopayResult.benefits,
              network_status: 'in_network',
              network_source: 'clinic_library_contract',
            }
          : fixedCopayResult.benefits,
      price_range: {
        ...fixedCopayResult.price_range,
        lines: [
          {
            ...fixedCopayResult.price_range.lines[0],
            benefits: { ...fixedCopayResult.price_range.lines[0].benefits, ...lineChange },
          },
        ],
      },
    }
    assert.equal(amountTextFor(stale), 'Insured amount unavailable', JSON.stringify(lineChange))
  }
  const wrongCptMember = {
    ...fixedCopayResult,
    price_range: {
      ...fixedCopayResult.price_range,
      lines: [
        {
          ...fixedCopayResult.price_range.lines[0],
          benefits: {
            ...fixedCopayResult.price_range.lines[0].benefits,
            identity: { status: 'matched', binding: 'another-member', match_version: 2 },
          },
        },
      ],
    },
  }
  const wrongCptMemberModel = deriveCommercialEstimateView({
    result: wrongCptMember,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert(
    wrongCptMemberModel.summaryExplanation.includes('CPT 99203 benefits do not match this patient'),
  )
  assert.equal(
    amountTextFor({
      ...fixedCopayResult,
      benefits: { ...fixedCopayResult.benefits, coverage_status_conflict: true },
      collection_guidance: {
        ...fixedCopayResult.collection_guidance,
        basis: 'claim_after_service',
        status: 'zero_advance',
        amount_cents: 0,
      },
      encounter_summary: { ...fixedCopayResult.encounter_summary, collect_today_cents: 0 },
    }),
    '$0.00',
  )
  const inconsistentSaved = {
    ...fixedCopayResult,
    benefits: {
      ...fixedCopayResult.benefits,
      network_status: 'in_network',
      network_source: 'clinic_library_contract',
    },
    collection_guidance: {
      ...fixedCopayResult.collection_guidance,
      basis: 'anticipated_services',
      amount_cents: 2500,
    },
    encounter_summary: {
      ...fixedCopayResult.encounter_summary,
      collect_today_cents: null,
      integrity_error: 'collection_amount_mismatch',
    },
    payment_explanation: {
      reason_code: 'insured_services',
      sentence: 'This $25.00 advance includes the saved office copay.',
    },
  }
  const inconsistentModel = deriveCommercialEstimateView({
    result: inconsistentSaved,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(inconsistentModel.amountText, 'Insured amount unavailable')
  assert(inconsistentModel.summaryExplanation.includes('saved collection differs'))
  assert(inconsistentModel.summaryExplanation.includes('Rerun the insured estimate'))
  assert(!inconsistentModel.summaryExplanation.includes('$25.00'))
  const sanitizedSaved = {
    ...inconsistentSaved,
    collection_guidance: {
      ...inconsistentSaved.collection_guidance,
      amount_cents: null,
      basis: 'calculation_conflict',
      status: 'unavailable',
    },
    payment_explanation: {
      reason_code: 'calculation_conflict',
      sentence:
        'The saved collection differs from the selected CPT patient share after recorded payment. Rerun the insured estimate before collecting.',
    },
  }
  const sanitizedModel = deriveCommercialEstimateView({
    result: sanitizedSaved,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(sanitizedModel.amountText, 'Insured amount unavailable')
  assert(sanitizedModel.summaryExplanation.includes('after recorded payment'))
  const unboundResult = {
    ...fixedCopayResult,
    identity: undefined,
    benefits: {
      ...fixedCopayResult.benefits,
      network_status: 'in_network',
      network_source: 'clinic_library_contract',
      oop_remaining_cents: 430000,
    },
  }
  const unboundModel = deriveCommercialEstimateView({
    result: unboundResult,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(unboundModel.amountText, 'Insured amount unavailable')
  assert.equal(unboundModel.networkLabel, 'Patient plan not matched')
  const oonTermsResult = {
    ...fixedCopayResult,
    identity: currentIdentity,
    benefits: {
      ...fixedCopayResult.benefits,
      network_status: 'out_of_network',
      network_source: undefined,
    },
  }
  const oonTermsModel = deriveCommercialEstimateView({
    result: oonTermsResult,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(oonTermsModel.networkLabel, 'Network not determined')
  assert.equal(oonTermsModel.networkTone, 'is-review')
  const oonProviderModel = deriveCommercialEstimateView({
    result: {
      ...oonTermsResult,
      benefits: { ...oonTermsResult.benefits, network_source: 'payer_participation_message' },
    },
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(oonProviderModel.networkLabel, 'Out of network')
  const oonAllowedResult = {
    ...oonTermsResult,
    benefits: { ...oonTermsResult.benefits, network_source: 'payer_participation_message' },
    collection_guidance: {
      ...oonTermsResult.collection_guidance,
      amount_cents: 4000,
      basis: 'anticipated_services',
    },
    encounter_summary: {
      complete: false,
      collect_today_cents: 4000,
      estimated_total_patient_min_cents: null,
      estimated_total_patient_max_cents: null,
      components: {
        copay_cents: 0,
        deductible_cents: 0,
        coinsurance_cents: 4000,
        oop_adjustment_cents: 0,
      },
      coinsurance_base_cents: 10000,
      coinsurance_percent: '40',
    },
    payment_explanation: { reason_code: 'insured_services' },
    price_range: {
      complete: true,
      mode: 'services',
      lines: [
        {
          id: 'office',
          code: '99203',
          allowed_basis: 'payer_out_of_network',
          allowed_cents: 10000,
          rate_resolution: { status: 'RESOLVED' },
          benefits: { active: true },
          calculation: { patient_cents: 4000 },
        },
      ],
    },
  }
  const oonAllowedModel = deriveCommercialEstimateView({
    result: oonAllowedResult,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(oonAllowedModel.amountText, '$40.00')
  const oonAllowedPage = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: oonAllowedModel,
      result: oonAllowedResult,
    }),
  )
  assert(oonAllowedPage.includes('40% of $100.00 after deductible'))
  assert(!oonAllowedPage.includes('Estimated later balance'))
  const oonCalculation = renderToStaticMarkup(
    React.createElement(CommercialCalculationDetail, {
      model: oonAllowedModel,
      result: oonAllowedResult,
    }),
  )
  assert(oonCalculation.includes('Known out-of-network plan share'))
  assert(oonCalculation.includes('$40.00'))
  assert(!oonCalculation.includes('See collection action above'))
  assert(oonCalculation.includes('Extra doctor charges are still unknown'))
  const unboundOverview = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: unboundModel,
      result: unboundResult,
    }),
  )
  assert(!unboundOverview.includes('Applicable in-network plan limits'))
  for (const unusableBenefits of [
    { historical_only: true },
    { service_date_matches: false },
    { coverage_dates_invalid: true },
    { coverage_status_conflict: true },
    { network_conflict: true },
    { has_secondary: true },
    { source_errors: [{ kind: 'invalid_amount' }] },
  ]) {
    const pastFigures = renderToStaticMarkup(
      React.createElement(CommercialOverview, {
        model: {
          ...unboundModel,
          amount: null,
          guidance: {
            basis: 'missing_collection_inputs',
            patient_actions: [],
            assumptions: [],
          },
        },
        result: {
          ...unboundResult,
          identity: currentIdentity,
          benefits: {
            ...unboundResult.benefits,
            copay_cents: 3000,
            oop_annual_cents: 900000,
            ...unusableBenefits,
          },
        },
      }),
    )
    assert(!pastFigures.includes('Payer-reported plan figures'), JSON.stringify(unusableBenefits))
  }
  assert(
    !renderToStaticMarkup(
      React.createElement(ApplicableBenefits, {
        result: unboundResult,
      }),
    ).includes('$4,300.00'),
  )
  const mismatchedLimits = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: {
        readOnly: true,
        cash: false,
        amount: '$50.00',
        amountText: '$50.00',
        amountLabel: 'Previous estimate',
        amountParts: { copay_cents: 3000 },
        guidance: { basis: 'anticipated_services', patient_actions: [], assumptions: [] },
      },
      result: {
        ...base,
        identity: { status: 'review' },
        benefits: {
          ...base.benefits,
          network_status: 'in_network',
          oop_annual_cents: 900000,
        },
      },
    }),
  )
  assert(!mismatchedLimits.includes('Applicable in-network plan limits'))
  const claim = renderToStaticMarkup(
    React.createElement(CommercialCalculationDetail, {
      model: {
        readOnly: true,
        price: { complete: false, lines: [] },
        calculationReasons: [],
        cash: false,
        guidance: { basis: 'claim_after_service', assumptions: [] },
        amount: '$0.00',
        deferred: false,
        amountLabel: 'Insurance advance today',
        amountText: '$0.00',
        explanation: 'Collect $0 today under clinic policy.',
      },
      result: {
        ...base,
        benefits: { ...base.benefits, network_status: 'unknown' },
        planning_estimate: {
          basis: 'out_of_pocket_limit',
          amount_cents: 0,
          explanation: 'Old conditional estimate',
        },
        service_plan: { assumptions: [] },
      },
    }),
  )
  assert(claim.includes('Patient responsibility after claim'))
  assert(!claim.includes('Out-of-pocket limit reached'))
  assert(!claim.includes('Old conditional estimate'))
  const staleTotal = renderToStaticMarkup(
    React.createElement(CommercialCalculationDetail, {
      model: {
        readOnly: true,
        price: {
          complete: true,
          mode: 'services',
          patient_min_cents: 5000,
          patient_max_cents: 5000,
          lines: [{ calculation: { copay_cents: 5000 } }],
        },
        calculationReasons: [],
        cash: false,
        guidance: { basis: 'ecw_billing_note', assumptions: [] },
        amount: '$0.00',
        deferred: false,
        amountLabel: 'Collect at arrival',
        amountText: '$0.00',
        explanation: 'No payment at arrival under the visit billing note.',
      },
      result: {
        ...base,
        benefits: { ...base.benefits, network_status: 'in_network' },
        identity: currentIdentity,
        recovery: { reason_code: 'multiple_active_policies' },
        encounter_summary: { complete: false },
        price_range: {
          complete: true,
          mode: 'services',
          patient_min_cents: 5000,
          patient_max_cents: 5000,
          lines: [{ calculation: { patient_cents: 5000, copay_cents: 5000 } }],
        },
        planning_estimate: {
          basis: 'patient_obligations',
          amount_cents: 5000,
          explanation: 'Old saved patient total',
        },
        service_plan: { assumptions: [] },
      },
    }),
  )
  assert(staleTotal.includes('Complete patient responsibility'))
  assert(staleTotal.includes('See collection action above'))
  assert(!staleTotal.includes('Determined by processed claim'))
  assert(!staleTotal.includes('Old saved patient total'))
  assert(!staleTotal.includes('Copay applied'))
  assert(!staleTotal.includes('Estimated cost sharing'))
  assert(!staleTotal.includes('calculation-summary'))
  const { PayerAllowanceEntry } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/PayerAllowanceEntry.tsx',
  )
  const allowanceForm = renderToStaticMarkup(
    React.createElement(PayerAllowanceEntry, {
      result: {
        ...base,
        service_date: '2026-10-14',
        selected_ids: ['office'],
        codes: [{ id: 'office', code: '99203', units: 1 }],
        payer_allowances: {
          office: {
            unit_allowed_cents: 12000,
            channel: 'payer_portal',
            reference: 'synthetic quote 12345',
            valid_through: '2026-10-14',
          },
        },
      },
      busy: false,
      save: async () => true,
      viewEstimate: () => {},
    }),
  )
  assert(allowanceForm.includes('Selected CPT') && allowanceForm.includes('Allowed per unit'))
  assert(allowanceForm.includes('synthetic quote 12345') && allowanceForm.includes('$120.00'))
  console.log('PASS: unassigned member balances hidden; matched benefit rows retained')
} finally {
  await server.close()
}
