import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

const python = existsSync('../.venv/bin/python') ? '../.venv/bin/python' : 'python3'
const projections = JSON.parse(
  execFileSync(python, ['../testing/emit_uhc_ui_projections.py'], { encoding: 'utf8' }),
)
const expected = {
  regional_ppo: 'check UHC Regional PPO participation under a separate agreement',
  multiple_policies: 'rerun eligibility and a UHC Stedi coordination-of-benefits check',
  identity_mismatch: 'reconcile first name, surname, subscriber first name, subscriber surname',
}
const visibleText = (markup) =>
  markup.replace(/<[^>]*>/g, '').replace(/&(?:#x[0-9a-f]+|#\d+|[a-z]+);/gi, '')

const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
try {
  const { deriveCommercialEstimateView } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/deriveCommercialEstimateView.ts',
  )
  const { default: CommercialOverview } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialOverview.tsx',
  )
  const { coinsuranceFormula } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/coinsuranceFormula.ts',
  )
  const { selectedRequirementIssue } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/selectedRequirementIssue.ts',
  )
  const { firstSelectedCptIssue } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/selectedCptIssue.ts',
  )
  const uncheckedCpt = {
    identity: { status: 'review', missing_fields: ['member_id'] },
    price_range: { lines: [{ code: '95908', benefits: { active: null,
      identity: { status: 'review', missing_fields: ['member_id'] } } }] },
  }
  assert.equal(firstSelectedCptIssue(uncheckedCpt), null)
  uncheckedCpt.price_range.lines[0].calculation = { patient_cents: 1000 }
  assert(firstSelectedCptIssue(uncheckedCpt)?.includes('do not match this patient'))
  delete uncheckedCpt.price_range.lines[0].calculation
  uncheckedCpt.price_range.lines[0].benefits.active = true
  assert(firstSelectedCptIssue(uncheckedCpt)?.includes('do not match this patient'))
  assert.equal(
    coinsuranceFormula({
      encounter_summary: {
        components: { coinsurance_cents: 3000 },
        coinsurance_percent: null,
        coinsurance_base_cents: null,
        coinsurance_rounded_per_line: true,
      },
      price_range: { mode: 'services', lines: [] },
    }),
    null,
  )
  assert.equal(
    coinsuranceFormula({
      encounter_summary: { complete: false },
      price_range: { mode: 'services', lines: [] },
    }),
    null,
  )
  const duplicateCptRequirements = {
    payer: 'United Healthcare',
    price_range: {
      mode: 'services',
      lines: [
        {
          id: 'office-a',
          code: '99203',
          benefits: {
            requirements: {
              referral: {
                required: 'required',
                status: 'valid',
                cleared: true,
                reference: 'synthetic-referral',
                verified_at: '2026-10-14T12:00:00Z',
              },
            },
          },
        },
        {
          id: 'office-b',
          code: '99203',
          benefits: { requirements: { referral: { required: 'not_required' } } },
        },
      ],
    },
    requirement_evidence: {
      referral: { status: 'Required' },
      procedures: [
        { id: 'office-a', code: '99203', referral: { status: 'Required' } },
        { id: 'office-b', code: '99203', referral: { status: 'Not required' } },
      ],
    },
  }
  assert.equal(selectedRequirementIssue(duplicateCptRequirements), null)
  const unclearedDuplicateCpt = structuredClone(duplicateCptRequirements)
  unclearedDuplicateCpt.price_range.lines[0].benefits.requirements.referral.reference = ''
  assert.equal(selectedRequirementIssue(unclearedDuplicateCpt), null)
  unclearedDuplicateCpt.price_range.lines[0].benefits.requirements.referral.status = 'denied'
  assert(selectedRequirementIssue(unclearedDuplicateCpt)?.includes('CPT 99203'))
  const { default: CommercialCalculationDetail } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialCalculationDetail.tsx',
  )
  const { default: CommercialCoverageDetail } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialCoverageDetail.tsx',
  )
  const { default: CoverageRecovery } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CoverageRecovery.tsx',
  )
  const groupedResponse = renderToStaticMarkup(
    React.createElement(CoverageRecovery, {
      model: { readOnly: true, failed: false, recovering: false },
      result: {
        insurance_diagnosis: {
          identityStatus: 'REVIEW_REQUIRED',
          eligibilityStatus: 'PAYER_BENEFITS_RETURNED',
          pricingStatus: 'NOT_CALCULATED',
          activePolicyCount: 1,
          rawCandidateCount: 3,
          retainedCandidateCount: 3,
          verifiedCandidateCount: 2,
          pricingReasons: [],
        },
      },
    }),
  )
  assert(groupedResponse.includes('1 distinct active medical policy found'))
  assert(groupedResponse.includes('3 payer responses'))
  const routeInProgress = renderToStaticMarkup(
    React.createElement(CoverageRecovery, {
      model: { readOnly: true, failed: false, recovering: false },
      result: { recovery: { state: 'indeterminate', payer_routes: [
        { id: 'synthetic-route', name: 'Synthetic Payer' },
      ] } },
    }),
  )
  assert(!routeInProgress.includes('Payer suggestions'))
  assert(groupedResponse.includes('2 identity and coverage matches'))
  const duplicateChoices = renderToStaticMarkup(
    React.createElement(CoverageRecovery, {
      model: { readOnly: true, failed: false, candidateSuggestion: 'medical-first' },
      result: {
        recovery: {
          candidates: [
            { id: 'medical-first', payer: 'Synthetic Medical', verification_status: 'VERIFIED', policy_group_id: 'same-policy', policy_response_count: 2 },
            { id: 'medical-copy', payer: 'Synthetic Medical', verification_status: 'VERIFIED', policy_group_id: 'same-policy', policy_response_count: 2 },
            { id: 'dental', payer: 'Synthetic Dental' },
          ],
        },
      },
    }),
  )
  assert(duplicateChoices.includes('value="medical-first"'))
  assert(!duplicateChoices.includes('value="medical-copy"'))
  assert(duplicateChoices.includes('value="dental"'))
  assert(duplicateChoices.includes('2 saved payer responses describe this policy'))
  const { default: CommercialServicesDetail } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/CommercialServicesDetail.tsx',
  )
  const activeUnpriced = structuredClone(projections.multiple_policies.unapproved)
  activeUnpriced.identity = {
    status: 'matched',
    match_version: 2,
    binding: 'synthetic-current-binding',
  }
  activeUnpriced.benefits.identity = activeUnpriced.identity
  activeUnpriced.benefits.has_secondary = false
  activeUnpriced.recovery = {
    state: 'complete',
    outcome: 'no_match',
    reason_code: 'no_coverage_found',
    active_policy_count: 1,
  }
  activeUnpriced.coverage_status = 'VERIFIED'
  activeUnpriced.payment_mode = 'insurance'
  activeUnpriced.cash_fallback = undefined
  activeUnpriced.payment_explanation = {
    sentence:
      'UnitedHealthcare POS Choice Plus medical coverage was returned active; select the office CPT and verify the network and allowed amount before collecting.',
    reason_code: 'planned_cpt_unselected',
  }
  const activeModel = deriveCommercialEstimateView({
    result: activeUnpriced,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(activeModel.policyStatus, 'Coverage active')
  assert.equal(activeModel.amount, null)
  const activeOverview = renderToStaticMarkup(
    React.createElement(CommercialOverview, { model: activeModel, result: activeUnpriced }),
  )
  assert(activeOverview.includes('medical coverage was returned active'))
  assert(!activeOverview.includes('Self-pay estimate'))
  const choicePlus = JSON.parse(
    execFileSync(python, ['../testing/emit_uhc_ui_projections.py', '--choice-plus-unresolved'], {
      encoding: 'utf8',
    }),
  )
  assert.equal(choicePlus.collection_guidance.amount_cents, null)
  const choicePlusModel = deriveCommercialEstimateView({
    result: choicePlus,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  const choicePlusOverview = renderToStaticMarkup(
    React.createElement(CommercialOverview, { model: choicePlusModel, result: choicePlus }),
  )
  assert(choicePlusOverview.includes('UHC reported active POS CHOICE PLUS medical coverage'))
  assert(choicePlusOverview.includes('$0.00 professional office copay'))
  assert(choicePlusOverview.includes('20.00% in-network and 40.00% out-of-network coinsurance'))
  assert(choicePlusOverview.includes('individual deductible balance for CPT 99203 is unresolved'))
  assert(!choicePlusOverview.includes('Self-pay estimate'))
  const { ApplicableBenefits } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/EstimatePanels.tsx',
  )
  const { RequirementCard } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/EstimatePanels.tsx',
  )
  const { default: VisitServices } = await server.ssrLoadModule(
    '/src/features/commercial-estimate/VisitServices.tsx',
  )
  for (const [name, cases] of Object.entries(projections)) {
    for (const [policy, result] of Object.entries(cases)) {
      const model = {
        ...deriveCommercialEstimateView({
          result,
          appointments: [],
          selectedVisit: '',
          busy: null,
          error: '',
          readOnly: true,
          recovering: false,
        }),
        readOnly: true,
      }
      const overview = renderToStaticMarkup(
        React.createElement(CommercialOverview, { model, result }),
      )
      if (result.price_range.mode === 'alternatives') {
        assert(
          overview.includes(
            'Office E/M consultation · E/M alternatives 99202, 99203, 99204, 99205',
          ),
        )
        assert(!overview.includes('Established patient office consultation'))
        if (name === 'regional_ppo' && policy === 'unapproved') {
          const conflicting = { ...result, visit_type: 'established' }
          const conflictingModel = deriveCommercialEstimateView({
            result: conflicting,
            appointments: [],
            selectedVisit: '',
            busy: null,
            error: '',
            readOnly: true,
            recovering: false,
          })
          const conflictingOverview = renderToStaticMarkup(
            React.createElement(CommercialOverview, {
              model: conflictingModel,
              result: conflicting,
            }),
          )
          assert(conflictingOverview.includes('eCW visit type conflicts with the CPT alternatives'))
        }
      }
      const calculation = renderToStaticMarkup(
        React.createElement(CommercialCalculationDetail, { model, result }),
      )
      const coverageDetail = renderToStaticMarkup(
        React.createElement(CommercialCoverageDetail, { model, result }),
      )
      const serviceDetail = renderToStaticMarkup(
        React.createElement(CommercialServicesDetail, { model, result }),
      )
      assert(coverageDetail.includes('<details id="visit-detail-coverage"'))
      assert(serviceDetail.includes('<details id="visit-detail-services"'))
      assert(coverageDetail.includes('Detailed benefits, insurance &amp; card'))
      assert(serviceDetail.includes('Saved service plan'))
      if (name === 'multiple_policies' && policy === 'unapproved') {
        const recovery = renderToStaticMarkup(
          React.createElement(CoverageRecovery, { model: { ...model, readOnly: false }, result }),
        )
        assert(recovery.includes('Plan name not returned'))
        assert(recovery.includes('Saved discovery has no candidate ID'))
        assert(!recovery.includes('Check this policy'))
      }
      if (name === 'regional_ppo' && policy === 'unapproved') {
        for (const [state, outcome, expectedStatus, noBenefitsStatus] of [
          [
            'pending',
            undefined,
            'Payer benefits returned · policy search was pending',
            'Coverage check was pending',
          ],
          [
            'indeterminate',
            undefined,
            'Payer benefits returned · policy search outcome unknown',
            'Coverage check outcome unknown',
          ],
          [
            'rejected',
            'not_available',
            'Payer benefits returned · policy search could not complete',
            'Coverage check could not complete',
          ],
        ]) {
          const interrupted = {
            ...result,
            recovery: { ...result.recovery, state, outcome },
          }
          const interruptedModel = deriveCommercialEstimateView({
            result: interrupted,
            appointments: [],
            selectedVisit: '',
            busy: null,
            error: '',
            readOnly: true,
            recovering: false,
          })
          assert.equal(interruptedModel.policyStatus, expectedStatus)
          assert(
            renderToStaticMarkup(
              React.createElement(CommercialOverview, {
                model: interruptedModel,
                result: interrupted,
              }),
            ).includes(expectedStatus),
          )
          const noBenefitsModel = deriveCommercialEstimateView({
            result: { ...interrupted, benefits: { ...interrupted.benefits, active: null } },
            appointments: [],
            selectedVisit: '',
            busy: null,
            error: '',
            readOnly: true,
            recovering: false,
          })
          assert.equal(noBenefitsModel.policyStatus, noBenefitsStatus)
        }
        const diagnosed = {
          ...result,
          eligibility_error: 'provider_enrollment',
          eligibility_failure: {
            kind: 'provider_enrollment',
            detail: 'Old provider enrollment lookup failed.',
          },
          recovery: {
            ...result.recovery,
            payer_routes: [
              {
                id: 'synthetic-uhc-route',
                name: 'UnitedHealthcare',
                aliases: [],
                names: [],
                match_basis: 'exact_payer_name',
                eligibility: 'ENROLLMENT_REQUIRED',
              },
            ],
          },
          insurance_diagnosis: {
            identityStatus: 'VERIFIED',
            eligibilityStatus: 'BENEFITS_VERIFIED',
            pricingStatus: 'NOT_CALCULATED',
            pricingReasons: [],
            enrollment: { status: 'MISSING' },
          },
        }
        const activeView = renderToStaticMarkup(
          React.createElement(CoverageRecovery, { model, result: diagnosed }),
        )
        assert(!activeView.includes('Provider enrollment:'))
        assert(!activeView.includes('Provider enrollment required'))
        const activeModel = deriveCommercialEstimateView({
          result: diagnosed,
          appointments: [],
          selectedVisit: '',
          busy: null,
          error: '',
          readOnly: false,
          recovering: false,
        })
        assert(!activeModel.unavailableReason.includes('Old provider enrollment'))
        const noBenefitsResult = {
          ...diagnosed,
          benefits: { ...diagnosed.benefits, active: null },
        }
        const noBenefitsView = renderToStaticMarkup(
          React.createElement(CoverageRecovery, {
            model,
            result: noBenefitsResult,
          }),
        )
        assert(noBenefitsView.includes('Provider enrollment:'))
        assert(noBenefitsView.includes('Provider enrollment required'))
        const noBenefitsModel = deriveCommercialEstimateView({
          result: noBenefitsResult,
          appointments: [],
          selectedVisit: '',
          busy: null,
          error: '',
          readOnly: false,
          recovering: false,
        })
        assert(noBenefitsModel.unavailableReason.includes('Old provider enrollment'))
      }
      const services = renderToStaticMarkup(
        React.createElement(VisitServices, {
          result,
          readOnly: true,
          insuredAmountSupported: Boolean(model.amount),
        }),
      )
      const referral = renderToStaticMarkup(
        React.createElement(RequirementCard, {
          title: 'Referral',
          evidence: result.requirement_evidence.referral,
          icon: React.createElement('span'),
          readOnly: true,
          needsCodeSelection: result.price_range.mode === 'alternatives',
          plannedCodes: result.codes.map((row) => row.code),
          payerName: result.payer,
        }),
      )
      const authorization = renderToStaticMarkup(
        React.createElement(RequirementCard, {
          title: 'Prior authorization',
          evidence: result.requirement_evidence.authorization,
          icon: React.createElement('span'),
          readOnly: true,
          needsCodeSelection: result.price_range.mode === 'alternatives',
          plannedCodes: result.codes.map((row) => row.code),
          payerName: result.payer,
        }),
      )
      assert.equal(result.requirement_evidence.referral.status, 'Verification required')
      assert.equal(result.requirement_evidence.authorization.status, 'Verification required')
      assert(
        !visibleText(referral).includes(';') && !visibleText(authorization).includes(';'),
        `${name}: requirement text has no semicolon-joined instructions`,
      )
      const requirementAction =
        result.price_range.mode === 'alternatives'
          ? '<strong>Choose CPT level</strong>'
          : '<strong>Document the planned CPT in eCW</strong>'
      assert(
        referral.includes(requirementAction) && authorization.includes(requirementAction),
        `${name}: requirement cards request the actual missing CPT`,
      )
      assert(result.benefits.active === true, `${name}: saved payer benefits retained`)
      assert(
        result.payment_explanation.sentence.includes(expected[name]),
        `${name}: exact staff action`,
      )
      assert(!result.payment_explanation.sentence.includes(';'), `${name}: one-line reason`)
      const sharedRequirement =
        result.price_range.mode === 'alternatives'
          ? 'Choose an anticipated CPT level to narrow these rules'
          : 'Document the planned CPT in eCW'
      assert(
        overview.includes(`Referral and prior authorization<strong>${sharedRequirement}</strong>`),
        `${name}: one combined requirement status`,
      )
      assert(!overview.includes('<strong>Choose CPT level</strong>'))
      assert.equal(
        (overview.match(/<strong>Document the planned CPT in eCW<\/strong>/g) || []).length,
        result.price_range.mode === 'alternatives' ? 0 : 1,
      )
      if (policy === 'approved') {
        assert.equal(
          result.collection_guidance.status,
          'zero_advance',
          `${name}: approved clinic policy`,
        )
        assert.equal(model.amountText, '$0.00', `${name}: approved clinic advance`)
        assert(overview.includes('Collect $0 today:'), `${name}: explanation below amount`)
      } else {
        assert.equal(
          result.collection_guidance.status,
          'action_required',
          `${name}: unresolved calculation is not an approved deferral`,
        )
        assert.equal(
          model.amountText,
          'Insured amount unavailable',
          `${name}: no invented insured amount`,
        )
        assert(overview.includes(expected[name]), `${name}: missing input shown below amount`)
      }
      assert(
        !overview.includes('Applicable in-network plan limits'),
        `${name}: unsourced plan limit hidden`,
      )
      if (policy === 'approved') {
        assert(
          overview.includes('<details class="summary-assumptions">'),
          `${name}: supported collection policy retains details`,
        )
      } else if (name === 'identity_mismatch') {
        assert(
          overview.includes('<summary>Patient steps</summary>'),
          `${name}: distinct patient action remains available`,
        )
      } else {
        assert(
          !overview.includes('summary-assumptions'),
          `${name}: no empty repeated assumptions panel`,
        )
      }
      if (policy !== 'approved') {
        assert(!overview.includes('Follow the action above before deciding what to collect.'))
        assert(!overview.includes('The insurance estimate is not available yet.'))
      }
      assert(
        calculation.includes('<details id="visit-detail-calculation"'),
        `${name}: calculation details are collapsed by default`,
      )
      if (policy === 'approved') {
        assert(calculation.includes('Patient responsibility after claim'))
      } else {
        assert(calculation.includes('See collection action above'))
        assert(!calculation.includes('Determined by processed claim'))
      }
      const benefitDetail = renderToStaticMarkup(
        React.createElement(ApplicableBenefits, { result }),
      )
      if (name === 'regional_ppo') {
        assert.equal((benefitDetail.match(/Applicable copay/g) || []).length, 1)
        assert.equal((benefitDetail.match(/Deductible remaining/g) || []).length, 1)
        assert.equal((benefitDetail.match(/Coinsurance/g) || []).length, 1)
        assert(benefitDetail.includes('Conflicting annual out-of-pocket rows'))
        assert(benefitDetail.includes('$9,000.00'))
        assert(benefitDetail.includes('Plan 1 · individual · in and out of network'))
        assert(benefitDetail.includes('effective dates not returned'))
        const dated = structuredClone(result)
        const conflictId = dated.benefits.oop_annual_conflict_ids[0]
        dated.benefits.reported_options.find((row) => row.id === conflictId).effective_dates = {
          'benefit row.benefitBegin': '2026-01-01',
        }
        const datedDetail = renderToStaticMarkup(
          React.createElement(ApplicableBenefits, { result: dated }),
        )
        assert(datedDetail.includes('effective dates benefit row benefit begin 2026-01-01'))
        assert.equal((benefitDetail.match(/Out-of-pocket remaining/g) || []).length, 1)
        assert(benefitDetail.includes('Shared office-visit terms for CPT alternatives'))
        assert(benefitDetail.includes('conflicting annual individual out-of-pocket maximum'))
      }
      const benefitAction =
        name === 'multiple_policies'
          ? 'Resolve patient identity and payer order before applying member balances.'
          : name === 'identity_mismatch'
            ? 'Resolve patient identity before applying member balances.'
            : 'The clinician-documented CPT is needed to apply them.'
      assert(
        benefitDetail.includes(benefitAction),
        `${name}: benefit action names only the actual blocker`,
      )
      if (name === 'multiple_policies') {
        assert(
          services.includes('Patient match and payer order unresolved'),
          `${name}: both payer blockers shown`,
        )
      }
      if (result.price_range.mode === 'alternatives') {
        assert(services.includes('Select one CPT'), `${name}: planned E/M level remains unselected`)
        assert(
          !services.includes('Bill patient share after claim'),
          `${name}: alternatives are not separate charges`,
        )
      }
      if (name !== 'regional_ppo') {
        assert(
          benefitDetail.includes('Payer benefit evidence'),
          `${name}: payer rows retained as evidence`,
        )
        assert(
          !overview.includes('payer-reported-terms'),
          `${name}: wrong-member balances not applied`,
        )
        assert(!benefitDetail.includes('Conflicting annual out-of-pocket rows'))
      } else {
        assert(overview.includes('Payer-reported benefit terms'))
        assert(overview.includes('Individual out-of-pocket remaining</dt><dd>$8,500.00'))
        assert(overview.includes('Payer rows conflict'))
      }
    }
  }
  const excluded = structuredClone(projections.regional_ppo.unapproved)
  excluded.network_evidence = {
    decision: 'excluded_under_available_agreement',
    document_id: 'synthetic-agreement',
    locator: 'Page 7',
    reason: 'Regional Medicare Advantage PPO is excluded under this agreement.',
  }
  excluded.payment_explanation = {
    sentence: 'No insured amount is ready because the exact UHC Regional Medicare Advantage PPO product is excluded from NMCT’s uploaded agreement; check for a separate applicable agreement.',
    reason_code: 'clinic_agreement_exclusion',
  }
  const excludedModel = deriveCommercialEstimateView({
    result: excluded, appointments: [], selectedVisit: '', busy: null,
    error: '', readOnly: true, recovering: false,
  })
  const excludedOverview = renderToStaticMarkup(
    React.createElement(CommercialOverview, { model: excludedModel, result: excluded }),
  )
  assert(excludedOverview.includes('Excluded under uploaded agreement'))
  assert(excludedOverview.includes('Regional Medicare Advantage PPO product is excluded'))
  const supported = JSON.parse(
    execFileSync(python, ['../testing/emit_uhc_ui_projections.py', '--supported'], {
      encoding: 'utf8',
    }),
  )
  const supportedModel = {
    ...deriveCommercialEstimateView({
      result: supported,
      appointments: [],
      selectedVisit: '',
      busy: null,
      error: '',
      readOnly: true,
      recovering: false,
    }),
    readOnly: true,
  }
  const supportedOverview = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: supportedModel,
      result: supported,
    }),
  )
  const supportedServices = renderToStaticMarkup(
    React.createElement(VisitServices, {
      result: supported,
      readOnly: true,
      insuredAmountSupported: Boolean(supportedModel.amount),
    }),
  )
  assert.equal(supported.price_range.patient_max_cents, 8000)
  assert.equal(supported.collection_guidance.amount_cents, 8000)
  assert.equal(supported.encounter_summary.collect_today_cents, 8000)
  assert.equal(supportedModel.amountText, '$80.00')
  assert(
    supportedOverview.includes(
      'United Healthcare is treated as in-network for this visit: collect $80.00 now: the patient owes $50.00 deductible and $30.00 coinsurance, calculated as 20% of $150.00 based on the clinic’s contracted CPT prices.',
    ),
  )
  assert(supportedOverview.includes('What the arrival amount includes'))
  assert(supportedOverview.includes('Deductible</dt><dd>$50.00'))
  assert(supportedOverview.includes('Coinsurance</dt><dd>$30.00'))
  assert(supportedOverview.includes('20% of $150.00 after deductible'))
  assert(supportedOverview.includes('Total to collect now</dt><dd>$80.00'))
  assert(supportedOverview.includes('Estimated patient cost for this visit'))
  assert(supportedOverview.includes('Individual out-of-pocket maximum'))
  assert(supportedOverview.includes('$1,000.00'))
  assert(supportedOverview.includes('Individual out-of-pocket remaining'))
  assert(supportedOverview.includes('Planned CPTs · referral and prior authorization'))
  assert(visibleText(supportedOverview).includes('CPT 99203: referral status not verified'))
  assert(visibleText(supportedOverview).includes('CPT 93000: referral status not verified'))
  assert(!supportedOverview.includes('Service not established'))
  assert(supportedOverview.includes('<h3>Planned services</h3>'))
  assert(!supportedOverview.includes('Why and sources'))
  assert(!supportedOverview.includes('This $80.00 advance includes $50.00 deductible'))
  assert(!supported.payment_explanation.sentence.includes(';'))
  assert(supportedServices.includes('CPT 99203') && supportedServices.includes('CPT 93000'))
  assert(supportedServices.includes('synthetic-scoped-contract'))
  const fourRequirements = JSON.parse(
    execFileSync(python, ['../testing/emit_uhc_ui_projections.py', '--four-requirements'], {
      encoding: 'utf8',
    }),
  )
  const fourModel = deriveCommercialEstimateView({
    result: fourRequirements,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  const fourOverview = renderToStaticMarkup(
    React.createElement(CommercialOverview, { model: fourModel, result: fourRequirements }),
  )
  assert.equal(fourModel.amountText, '$80.00')
  assert(fourOverview.includes('Network<strong>In network'))
  assert(fourOverview.includes('Individual out-of-pocket maximum'))
  assert(fourOverview.includes('Total to collect now</dt><dd>$80.00'))
  assert(fourOverview.includes('20% of $150.00 after deductible for CPTs with payable coinsurance'))
  assert(visibleText(fourOverview).includes('CPT 99203: no referral, no prior authorization'))
  assert(visibleText(fourOverview).includes('CPT 93000: no referral, prior authorization approved'))
  assert(visibleText(fourOverview).includes('CPT 95907: referral on file, no prior authorization'))
  assert(
    visibleText(fourOverview).includes('CPT 95886: referral on file, prior authorization approved'),
  )
  assert(!fourOverview.includes('Why and sources'))
  const conflictingCptRule = structuredClone(fourRequirements)
  conflictingCptRule.requirement_evidence.authorization = {}
  conflictingCptRule.price_range.lines[1].benefits.requirements.authorization = {
    required: 'not_required',
    status: 'not_applicable',
    cleared: true,
  }
  const conflictingCptModel = deriveCommercialEstimateView({
    result: conflictingCptRule,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(conflictingCptModel.amount, '$80.00')
  assert(
    visibleText(
      renderToStaticMarkup(
        React.createElement(CommercialOverview, { model: conflictingCptModel, result: conflictingCptRule }),
      ),
    ).includes('review prior authorization rule'),
  )
  const invalidMath = structuredClone(supported)
  invalidMath.encounter_summary.integrity_error = 'line_amount_mismatch'
  const invalidMathModel = deriveCommercialEstimateView({
    result: invalidMath,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(invalidMathModel.amount, null)
  assert(invalidMathModel.summaryExplanation.includes('saved CPT patient share differs'))
  const incompleteService = structuredClone(supported)
  incompleteService.price_range.service_issues = ['CPT 93000 has no planned unit count; select one']
  const incompleteServiceModel = deriveCommercialEstimateView({
    result: incompleteService,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(incompleteServiceModel.amount, null)
  assert(incompleteServiceModel.summaryExplanation.includes('CPT 93000 has no planned unit count'))
  assert(!incompleteServiceModel.summaryExplanation.includes(';'))
  const failedUpdate = deriveCommercialEstimateView({
    result: supported,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: 'The visit update failed.',
    readOnly: false,
    recovering: false,
  })
  const failedOverview = renderToStaticMarkup(
    React.createElement(CommercialOverview, { model: failedUpdate, result: supported }),
  )
  const failedCalculation = renderToStaticMarkup(
    React.createElement(CommercialCalculationDetail, { model: failedUpdate, result: supported }),
  )
  assert.equal(failedUpdate.amount, null)
  assert.equal(failedUpdate.amountText, 'Check visit details')
  assert.equal(failedUpdate.amountLabel, 'Previous estimate · needs recheck')
  assert(failedOverview.includes('The update failed. Recheck this visit before collecting.'))
  assert(!failedOverview.includes('What the arrival amount includes'))
  assert(!failedOverview.includes('Planned CPTs and coverage requirements'))
  assert(!failedCalculation.includes('What the patient may owe'))
  const failedCashResult = { ...supported, payment_mode: 'cash' }
  const failedCash = deriveCommercialEstimateView({
    result: failedCashResult,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: 'The visit update failed.',
    readOnly: true,
    recovering: false,
  })
  const failedCashDetail = renderToStaticMarkup(
    React.createElement(CommercialCalculationDetail, {
      model: failedCash,
      result: failedCashResult,
    }),
  )
  assert.equal(failedCash.amount, null)
  assert.equal(failedCash.amountLabel, 'Previous estimate · needs recheck')
  assert(failedCashDetail.includes('Cash price · Needs recheck'))
  const documented = structuredClone(supported)
  const noRequirement = { status: 'Not required', reason: 'Synthetic payer rule', sources: [] }
  const required = { status: 'Required', reason: 'Synthetic payer rule', sources: [] }
  documented.price_range.lines[1].benefits.requirements.referral = {
    required: 'required',
    status: 'valid',
    cleared: true,
    reference: 'synthetic-referral',
    verified_at: '2026-10-15T12:00:00Z',
  }
  documented.price_range.lines[1].benefits.requirements.authorization = {
    required: 'required',
    status: 'valid',
    cleared: true,
    reference: 'synthetic-approval',
    verified_at: '2026-10-15T12:00:00Z',
  }
  documented.requirement_evidence = {
    referral: required,
    authorization: { ...required, approval_status: 'Verified' },
    procedures: [
      { id: 'office', code: '99203', referral: noRequirement, authorization: noRequirement },
      {
        id: 'test',
        code: '93000',
        referral: required,
        authorization: { ...required, approval_status: 'Verified' },
      },
    ],
  }
  const documentedModel = deriveCommercialEstimateView({
    result: documented,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  const documentedOverview = renderToStaticMarkup(
    React.createElement(CommercialOverview, { model: documentedModel, result: documented }),
  )
  assert(visibleText(documentedOverview).includes('CPT 99203: no referral, no prior authorization'))
  assert(
    visibleText(documentedOverview).includes(
      'CPT 93000: referral on file, prior authorization approved',
    ),
  )
  assert(!documentedOverview.includes('Why and sources'))
  const unpricedRequirements = structuredClone(documented)
  unpricedRequirements.collection_guidance.amount_cents = null
  unpricedRequirements.collection_guidance.status = 'action_required'
  unpricedRequirements.encounter_summary.collect_today_cents = null
  const unpricedModel = deriveCommercialEstimateView({
    result: unpricedRequirements,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(unpricedModel.amount, null)
  const unpricedOverview = renderToStaticMarkup(
    React.createElement(CommercialOverview, { model: unpricedModel, result: unpricedRequirements }),
  )
  assert(
    visibleText(unpricedOverview).includes('CPT 93000: referral on file, prior authorization approved'),
  )
  const otherPatientRequirements = structuredClone(unpricedRequirements)
  otherPatientRequirements.identity.status = 'review'
  otherPatientRequirements.identity.binding = null
  const otherPatientModel = deriveCommercialEstimateView({
    result: otherPatientRequirements,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  const otherPatientOverview = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: otherPatientModel,
      result: otherPatientRequirements,
    }),
  )
  assert(!otherPatientOverview.includes('Planned CPTs and coverage requirements'))
  const lineOnly = structuredClone(documented)
  lineOnly.requirement_evidence = {}
  const lineOnlyModel = deriveCommercialEstimateView({
    result: lineOnly,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  const lineOnlyOverview = renderToStaticMarkup(
    React.createElement(CommercialOverview, { model: lineOnlyModel, result: lineOnly }),
  )
  const lineOnlyServices = renderToStaticMarkup(
    React.createElement(VisitServices, {
      result: lineOnly,
      readOnly: true,
      insuredAmountSupported: Boolean(lineOnlyModel.amount),
    }),
  )
  assert(
    visibleText(lineOnlyOverview).includes('CPT 93000: referral on file, prior authorization on file'),
  )
  assert(!visibleText(lineOnlyOverview).includes('CPT 93000: ask United Healthcare'))
  assert(lineOnlyServices.includes('CPT source reference and verification time recorded'))
  const conflictingRule = structuredClone(documented)
  conflictingRule.requirement_evidence.procedures[1].referral = noRequirement
  conflictingRule.requirement_evidence.referral = noRequirement
  const conflictingModel = deriveCommercialEstimateView({
    result: conflictingRule,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(conflictingModel.amount, '$80.00')
  const conflictingOverview = renderToStaticMarkup(
    React.createElement(CommercialOverview, { model: conflictingModel, result: conflictingRule }),
  )
  assert(visibleText(conflictingOverview).includes('review referral rule'))
  assert(conflictingOverview.includes('What the arrival amount includes'))
  assert(!conflictingOverview.includes('Referral<strong>Not required'))
  const deferredConflict = structuredClone(conflictingRule)
  deferredConflict.collection_guidance = {
    ...deferredConflict.collection_guidance,
    amount_cents: 0,
    status: 'zero_advance',
    basis: 'claim_after_service',
  }
  deferredConflict.encounter_summary.collect_today_cents = 0
  const deferredConflictModel = deriveCommercialEstimateView({
    result: deferredConflict,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(deferredConflictModel.amountText, '$0.00')
  const conflictingServices = renderToStaticMarkup(
    React.createElement(VisitServices, {
      result: conflictingRule,
      readOnly: true,
      insuredAmountSupported: false,
    }),
  )
  assert(conflictingServices.includes('CPT rule conflicts. Ask United Healthcare about CPT 93000'))
  const missingApprovalReference = structuredClone(documented)
  delete missingApprovalReference.price_range.lines[1].benefits.requirements.authorization.reference
  const missingApprovalModel = deriveCommercialEstimateView({
    result: missingApprovalReference,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  const missingApprovalOverview = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: missingApprovalModel,
      result: missingApprovalReference,
    }),
  )
  assert.equal(missingApprovalModel.amount, '$80.00')
  assert(missingApprovalOverview.includes('What the arrival amount includes'))
  assert(!missingApprovalOverview.includes('prior authorization approved'))
  const unapproved = structuredClone(documented)
  unapproved.requirement_evidence.authorization.approval_status = 'Not verified'
  const unapprovedModel = deriveCommercialEstimateView({
    result: unapproved,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(unapprovedModel.amount, '$80.00')
  const conflictingCptApproval = structuredClone(documented)
  conflictingCptApproval.requirement_evidence.procedures[1].authorization.approval_status =
    'Not verified'
  const conflictingCptApprovalModel = deriveCommercialEstimateView({
    result: conflictingCptApproval,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  assert.equal(conflictingCptApprovalModel.amount, '$80.00')
  const requiredAuthorization = JSON.parse(
    execFileSync(python, ['../testing/emit_uhc_ui_projections.py', '--required-authorization'], {
      encoding: 'utf8',
    }),
  )
  const requiredModel = deriveCommercialEstimateView({
    result: requiredAuthorization,
    appointments: [],
    selectedVisit: '',
    busy: null,
    error: '',
    readOnly: true,
    recovering: false,
  })
  const requiredOverview = renderToStaticMarkup(
    React.createElement(CommercialOverview, {
      model: requiredModel,
      result: requiredAuthorization,
    }),
  )
  assert.equal(requiredModel.amountText, '$80.00')
  assert.equal(requiredAuthorization.encounter_summary.estimated_total_patient_min_cents, 8000)
  assert(requiredOverview.includes('What the arrival amount includes'))
  console.log(
    'PASS: three deidentified UHC saved responses and two supported UHC calculations render',
  )
} finally {
  await server.close()
}
