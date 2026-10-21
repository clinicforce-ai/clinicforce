import { coveragePolicyStatus } from './coveragePolicyStatus'
import { money, range, visitDate, visitTime } from './format'
import { patientBenefitsBound as hasPatientBinding } from './identityBinding'
import { firstSelectedCptIssue } from './selectedCptIssue'
import { selectedRequirementIssue } from './selectedRequirementIssue'
import { visitServiceLabel } from './visitServiceLabel'
import type { CommercialEstimateActions } from './useCommercialEstimateActions'

export function deriveCommercialEstimateView({
  result,
  appointments,
  selectedVisit,
  busy,
  error,
  readOnly,
  recovering,
}: CommercialEstimateActions) {
  const selected = appointments.find((visit) => visit.id === selectedVisit)
  const benefits = result?.benefits
  const patientBenefitsBound = hasPatientBinding(result?.identity)
  const participationConflict =
    benefits?.network_conflict === true ||
    benefits?.review_reasons?.some((reason) =>
      reason.toLowerCase().includes('provider-network evidence reports nonparticipation'),
    ) === true
  const networkConfirmed =
    benefits?.network_status === 'in_network' &&
    !participationConflict &&
    ['clinic_library_contract', 'payer_participation_message'].includes(
      benefits.network_source || '',
    )
  const price = result?.price_range
  const outOfNetworkConfirmed =
    benefits?.network_status === 'out_of_network' &&
    [
      'payer_participation_message',
      'clinic_nonparticipation_rule',
      'clinic_contract_inventory',
    ].includes(benefits.network_source || '') &&
    !participationConflict
  const outOfNetworkAllowance =
    outOfNetworkConfirmed &&
    price?.mode === 'services' &&
    price.complete === true &&
    !!price.lines?.length &&
    price.lines.every(
      (line) =>
        ['payer_out_of_network', 'cms_original_medicare_estimate'].includes(
          line.allowed_basis || '',
        ) &&
        Number.isInteger(line.allowed_cents) &&
        line.allowed_cents! >= 0 &&
        line.rate_resolution?.status === 'RESOLVED' &&
        !!line.calculation,
    )
  const selectedCptIssue = firstSelectedCptIssue(result)
  const servicePlanIssue = price?.service_issues?.find((issue) => issue.trim())
  const servicePlanAction = servicePlanIssue
    ? `${servicePlanIssue.replaceAll(';', ',').replace(/\.$/, '')}. Correct the selected service plan and rerun the insured estimate.`
    : null
  const topCoverageIssue =
    benefits?.coverage_status_conflict === true
      ? 'The payer returned conflicting active and inactive coverage for this visit. Ask which status applies before collecting.'
      : benefits?.coverage_dates_invalid === true
        ? 'The payer coverage date could not be read. Obtain visit-date eligibility before collecting.'
        : null
  const payerEvidenceSafe = !topCoverageIssue && !selectedCptIssue && !servicePlanAction
  const financialSourceValid =
    benefits?.historical_only !== true &&
    !result?.encounter_summary?.integrity_error &&
    !benefits?.source_errors?.length &&
    !price?.lines?.some((line) => line.benefits.source_errors?.length)
  const officeCopayLine = price?.lines?.find(
    (line) => /^992(?:0[2-5]|1[2-5])$/.test(line.code) && line.benefits.copay_id,
  )
  const copayEvidenceId = officeCopayLine?.benefits.copay_id || benefits?.copay_id
  const copaySource =
    officeCopayLine?.benefit_source || result?.benefit_source || 'payer eligibility'
  const staleEnrollmentFailure =
    benefits?.active === true &&
    !benefits.historical_only &&
    result?.eligibility_failure?.kind === 'provider_enrollment'
  const failure = staleEnrollmentFailure ? undefined : result?.eligibility_failure
  const patientPrice =
    benefits?.active === true &&
    benefits.service_date_matches !== false &&
    payerEvidenceSafe &&
    financialSourceValid &&
    networkConfirmed &&
    result?.encounter_summary?.complete !== false &&
    patientBenefitsBound &&
    !benefits.has_secondary &&
    result?.recovery?.reason_code !== 'multiple_active_policies' &&
    price?.complete &&
    price?.patient_min_cents != null &&
    price.patient_max_cents != null
      ? range(price.patient_min_cents, price.patient_max_cents)
      : null
  const calculationReasons = [
    ...new Set((price?.lines || []).map((line) => line.benefits.benefit_summary).filter(Boolean)),
  ]
  const policySources = [
    ...new Map(
      (price?.lines || [])
        .filter((line) => line.benefits.policy_evidence)
        .map((line) => [line.benefits.policy_evidence!.url, line.benefits.policy_evidence!]),
    ).values(),
  ]
  const cashPrice =
    price?.cash_complete && price.cash_min_cents != null && price.cash_max_cents != null
      ? range(price.cash_min_cents, price.cash_max_cents)
      : null
  const planName =
    result?.plan_details?.stedi.names?.join(' · ') ||
    result?.plan_details?.ecw.plan?.filter((value) => value !== 'CI').join(' · ') ||
    (result?.plan !== 'CI' ? result?.plan : null)
  const planSource = result?.plan_details?.stedi.names?.length
    ? `${result.benefit_source || result.plan_details.stedi.source || 'Payer'} eligibility response`
    : planName
      ? 'eCW policy record'
      : null
  const planTypes = [
    /\bHMO\b|health maintenance organization/i.test(planName || '') ? 'HMO' : null,
    /\bPPO\b|preferred provider (?:option|organization)/i.test(planName || '') ? 'PPO' : null,
  ].filter(Boolean)
  const planType = planTypes.length === 1 ? planTypes[0] : null
  const blueCross = /blue cross|bcbs/i.test(result?.payer || '')
  const blueCard = /bluecard/i.test(
    [planName, ...Object.values(result?.plan_details?.ecw || {}).flat()].filter(Boolean).join(' '),
  )
  const payerSource =
    result?.payer_source ||
    (result?.payer &&
    result.coverage_records?.some((row) =>
      row.payer?.some((name) => name.trim().toLowerCase() === result.payer?.trim().toLowerCase()),
    )
      ? 'Coverage.payor'
      : null)
  const payerSubscriber = result?.insurance_details?.payer_subscriber
  const payerPatient = result?.insurance_details?.payer_patient
  const policyDates = result?.insurance_details?.payer_coverage_period?.start
    ? result.insurance_details.payer_coverage_period
    : result?.insurance_details?.coverage_period

  const tooEarly = result?.eligibility_timing?.status === 'too_early'
  const candidateStates = [
    ...new Set(
      result?.recovery?.candidates?.map((row) => row.coverage_evidence?.status).filter(Boolean),
    ),
  ]
  const candidateStatus = candidateStates.length === 1 ? candidateStates[0] : undefined
  const policyStatus = coveragePolicyStatus({
    result,
    tooEarly,
    readOnly,
    busy,
    failureKind: failure?.kind,
    candidateStatus,
    patientBenefitsBound,
  })
  const coverageSource =
    benefits?.active != null || benefits?.coverage_evidence
      ? result?.benefit_source
        ? `Payer eligibility via ${result.benefit_source}`
        : 'Payer eligibility source not retained'
      : result?.recovery?.state
        ? result.insurance_diagnosis?.coverageSource === 'stedi_discovery'
          ? 'Stedi coverage discovery · payer benefits unavailable'
          : 'Coverage discovery · payer benefits unavailable'
        : result?.ecw_coverage_active
          ? 'eCW policy record · payer benefits unavailable'
          : 'No verified coverage source'
  const contractEvidence = result?.network_evidence || result?.mapping_candidate?.contract
  const contractPage = contractEvidence?.locator?.match(/^Page (\d+)(?:,|$)/)?.[1]
  const contractDocumentId = contractEvidence?.document_id || contractEvidence?.related_document_id
  const contractUrl =
    result?.payment_mode !== 'cash' && contractDocumentId
      ? `/api/staff/library/${encodeURIComponent(contractDocumentId)}/document?preview=true${contractPage ? `#page=${contractPage}` : ''}`
      : null

  const cash = result?.payment_mode === 'cash'
  const coverageTone =
    policyStatus === 'Coverage active' ||
    policyStatus === 'Coverage active · insured price unavailable'
      ? 'is-confirmed'
      : !result ||
          tooEarly ||
          result.recovery?.state === 'pending' ||
          failure?.kind === 'provider_enrollment' ||
          (cash && !result.cash_fallback)
        ? ''
        : 'is-missing'
  const calculating =
    (!!busy && busy !== 'delete') || recovering || (!!selected && !result && !error)
  const previousEstimate = !!error && !!result
  const guidance =
    result?.collection_guidance?.status !== 'deferred' ? result?.collection_guidance : undefined
  const contractCandidate =
    benefits?.network_status === 'unknown' ? result?.mapping_candidate?.network : null
  const networkLabel =
    result?.network_evidence?.decision === 'out_of_network_by_clinic_contract_inventory'
      ? 'Out of network for estimation · clinic inventory'
      : result?.network_evidence?.decision === 'excluded_under_available_agreement'
        ? 'Excluded under uploaded agreement'
        : result?.network_evidence?.decision === 'in_network_contract_supported' &&
            patientBenefitsBound
          ? 'Contract indicates in network'
          : benefits?.active === true &&
              !patientBenefitsBound &&
              ['in_network', 'out_of_network'].includes(benefits.network_status || '')
            ? 'Patient plan not matched'
            : benefits?.network_status === 'in_network'
              ? networkConfirmed
                ? 'In network'
                : 'Network not determined'
              : benefits?.network_status === 'out_of_network'
                ? outOfNetworkConfirmed
                  ? 'Out of network'
                  : 'Network not determined'
                : contractCandidate
                  ? 'Contract match unavailable'
                  : 'Not determined'
  const networkSource =
    result?.network_evidence?.decision === 'in_network_contract_supported'
      ? 'Dated group agreement and product-network rule · clinician participation pending'
      : benefits?.network_source === 'clinic_library_contract'
        ? 'Current Clinical Library contract'
        : benefits?.network_source === 'clinic_library_absent'
          ? 'No contract linked to this exact payer product and provider'
          : benefits?.network_source === 'clinic_nonparticipation_rule'
            ? 'Current scoped clinic nonparticipation rule'
            : benefits?.network_source === 'clinic_contract_inventory'
              ? 'Certified clinic contract inventory · not payer-confirmed'
              : guidance?.network_source
  const networkTone =
    !result || tooEarly || result.recovery?.state === 'pending'
      ? ''
      : benefits?.active === true && !patientBenefitsBound
        ? 'is-review'
        : benefits?.network_status === 'in_network'
          ? networkConfirmed
            ? 'is-confirmed'
            : 'is-review'
          : benefits?.network_status === 'out_of_network'
            ? outOfNetworkConfirmed
              ? 'is-missing'
              : 'is-review'
            : 'is-review'
  const unavailableReason = error
    ? 'Current benefits could not be loaded.'
    : failure?.detail || 'The insured advance has not been calculated for the selected services.'
  const independentOfficeCopay =
    guidance?.basis === 'office_copay' &&
    (price?.lines || []).some(
      (line) => /^992(?:0[2-5]|1[2-5])$/.test(line.code) && line.benefits.network_independent,
    ) &&
    (price?.lines || [])
      .filter((line) => /^992(?:0[2-5]|1[2-5])$/.test(line.code))
      .every((line) => line.benefits.network_independent)
  const outOfNetworkCollection =
    outOfNetworkConfirmed && (outOfNetworkAllowance || guidance?.basis === 'office_copay')
  const currentPayerAmount =
    benefits?.active === true &&
    benefits.service_date_matches !== false &&
    payerEvidenceSafe &&
    financialSourceValid &&
    !participationConflict &&
    patientBenefitsBound &&
    !benefits.has_secondary &&
    result?.recovery?.reason_code !== 'multiple_active_policies' &&
    (networkConfirmed || outOfNetworkCollection || independentOfficeCopay)
  const clinicInstructionAmount =
    guidance?.basis === 'claim_after_service' ||
    (guidance?.basis === 'ecw_billing_note' &&
      (guidance.amount_cents === 0 || benefits?.active !== true))
  const requirementIssue = !cash ? selectedRequirementIssue(result) : null
  const supportedAmount =
    !previousEstimate &&
    !['deferred_billing', 'action_required'].includes(guidance?.status || '') &&
    guidance?.amount_cents != null &&
    Number.isFinite(guidance.amount_cents) &&
    guidance.amount_cents >= 0 &&
    (!requirementIssue || clinicInstructionAmount) &&
    (cash || clinicInstructionAmount || currentPayerAmount) &&
    (result?.encounter_summary?.collect_today_cents === undefined ||
      result.encounter_summary.collect_today_cents === guidance.amount_cents)
  const emgBase = result?.emg_option
  const emgCostSupported =
    emgBase?.calculation_version === 2 &&
    !previousEstimate &&
    benefits?.active === true &&
    benefits.service_date_matches !== false &&
    payerEvidenceSafe &&
    financialSourceValid &&
    !participationConflict &&
    !requirementIssue &&
    patientBenefitsBound &&
    !benefits.has_secondary &&
    result?.recovery?.reason_code !== 'multiple_active_policies' &&
    (networkConfirmed || outOfNetworkAllowance) &&
    !result?.encounter_summary?.integrity_error &&
    ['patient_obligations', 'anticipated_services', 'out_of_pocket_limit'].includes(
      guidance?.basis || '',
    )
  const emg =
    emgBase && !emgCostSupported
      ? {
          ...emgBase,
          additional_min_cents: null,
          additional_max_cents: null,
          total_min_cents: null,
          total_max_cents: null,
          message:
            'Recalculate the testing scenario after resolving the payer or visit issue above.',
        }
      : emgBase
  const billingInstruction = guidance?.billing_note?.kind === 'no_collection'
  const emgScenario = !billingInstruction && !cash && emg?.requested && !emg.included
  const cashAmount =
    cash && supportedAmount && guidance?.basis === 'practice_cash_price'
      ? guidance.range_min_cents != null && guidance.range_max_cents != null
        ? range(guidance.range_min_cents, guidance.range_max_cents)
        : money(guidance.amount_cents!)
      : cashPrice
  const amount =
    calculating || previousEstimate
      ? null
      : cash
        ? cashAmount
        : supportedAmount
          ? money(guidance!.amount_cents!)
          : null
  const deferred = !cash && guidance?.basis === 'claim_after_service' && !!amount
  const amountText =
    readOnly && !result?.collection_guidance && !cash
      ? 'Not retained'
      : calculating
        ? 'Calculating…'
        : (amount ??
          (error
            ? 'Check visit details'
            : tooEarly
              ? 'Too early to check'
              : cash
                ? 'Confirm clinic price'
                : 'Insured amount unavailable'))
  const amountLabel = previousEstimate
    ? 'Previous estimate · needs recheck'
    : readOnly
      ? 'Recorded recommendation at this visit'
      : error
        ? 'Visit needs review'
        : result?.cash_fallback
          ? 'Self-pay option for this visit'
          : !amount && !cash
            ? 'Collection decision needed'
            : 'Amount to collect now'
  const amountParts = result?.encounter_summary?.components
  const officeDeductible = guidance?.services.find(
    (service) => service.code === officeCopayLine?.code,
  )?.deductible_applicability
  const cashExplanation = `${result?.cash_fallback?.reason ? `${result.cash_fallback.reason} ` : ''}${result?.eligibility_error === 'payer_routing_unresolved' && failure?.detail ? `${failure.detail} ` : ''}${result?.cash_quote?.message || 'The cash price is not established.'}`
  const staleInsuredAmount = !cash && guidance?.amount_cents != null && !supportedAmount
  const integrityError = result?.encounter_summary?.integrity_error
  const integrityAction =
    integrityError === 'patient_total_mismatch'
      ? 'The saved patient total differs from its selected CPT calculations. Rerun the insured estimate before collecting.'
      : integrityError === 'line_amount_mismatch'
        ? 'A saved CPT patient share differs from its copay, deductible and coinsurance calculation. Rerun the insured estimate before collecting.'
        : integrityError === 'collection_amount_mismatch'
          ? 'The saved collection differs from the selected CPT patient share after recorded payment. Rerun the insured estimate before collecting.'
          : null
  const baseExplanation = previousEstimate
    ? `The last saved result${result?.calculated_at ? ` from ${visitDate(result.calculated_at)} · ${visitTime(result.calculated_at)}` : ''} is shown. The update failed. Recheck this visit before collecting.`
    : calculating
      ? 'Reading the visit, insurance and applicable benefits to calculate the amount.'
      : staleInsuredAmount
        ? integrityAction ||
          requirementIssue ||
          topCoverageIssue ||
          selectedCptIssue ||
          servicePlanAction ||
          (benefits?.historical_only
            ? 'The protected saved payer response could not be reopened. Rerun the payer check before collecting.'
            : null) ||
          (participationConflict
            ? 'Provider participation evidence conflicts for this plan. Ask the payer which network tier applies to the rendering provider.'
            : null) ||
          (networkConfirmed
            ? 'The saved advance conflicts with current patient or collection evidence, so rerun the insured estimate before collecting.'
            : 'The saved advance lacks a current plan and provider participation source, so link the exact plan and rendering provider and rerun the insured estimate.')
        : result?.payment_explanation?.sentence ||
          (readOnly && !result?.collection_guidance && !cash
            ? 'The original collection recommendation was not retained. The saved benefit and calculation details are shown below.'
            : deferred
              ? `${guidance?.reason || unavailableReason} No insurance advance is due today under the clinic policy. Any patient responsibility follows the claim.`
              : cash
                ? cashPrice && !error
                  ? cashExplanation
                  : 'The clinic cash price could not be calculated for this service plan.'
                : guidance?.reason || unavailableReason)
  const explanation =
    emgScenario && !calculating && !/nerve testing/i.test(baseExplanation)
      ? `${baseExplanation} ${emg.total_min_cents != null ? 'The nerve-testing total below is for planning only' : 'Nerve testing is excluded because its patient cost is unknown'}.`
      : baseExplanation
  const summaryExplanation = explanation
  const provider = result?.appointment_context?.rendering_provider?.name
  const anticipatedService = visitServiceLabel(result)
  return {
    selected,
    benefits,
    price,
    officeCopayLine,
    copayEvidenceId,
    copaySource,
    failed: !!result?.eligibility_error && !staleEnrollmentFailure,
    failure,
    patientPrice,
    calculationReasons,
    policySources,
    cashPrice,
    planName,
    planSource,
    planType,
    blueCross,
    blueCard,
    payerSource,
    payerSubscriber,
    payerPatient,
    policyDates,
    tooEarly,
    candidateStatus,
    policyStatus,
    coverageSource,
    contractEvidence,
    contractPage,
    contractDocumentId,
    contractUrl,
    cash,
    coverageTone,
    calculating,
    previousEstimate,
    guidance,
    contractCandidate,
    networkLabel,
    networkSource,
    networkTone,
    unavailableReason,
    requirementIssue,
    supportedAmount,
    emg,
    billingInstruction,
    emgScenario,
    cashAmount,
    amount,
    deferred,
    amountText,
    amountLabel,
    amountParts,
    officeDeductible,
    cashExplanation,
    baseExplanation,
    explanation,
    summaryExplanation,
    provider,
    anticipatedService,
  }
}
