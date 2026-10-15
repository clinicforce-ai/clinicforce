import { money, range, visitDate, visitTime } from './format'
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
  const price = result?.price_range
  const officeCopayLine = price?.lines?.find(
    (line) => /^992(?:0[2-5]|1[2-5])$/.test(line.code) && line.benefits.copay_id,
  )
  const copayEvidenceId = officeCopayLine?.benefits.copay_id || benefits?.copay_id
  const copaySource =
    officeCopayLine?.benefit_source || result?.benefit_source || 'payer eligibility'
  const failed = !!result?.eligibility_error
  const failure = result?.eligibility_failure
  const patientPrice =
    benefits?.active === true &&
    benefits.network_status === 'in_network' &&
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
  const policyStatus = tooEarly
    ? 'Check closer to visit'
    : result?.recovery?.state === 'pending'
      ? readOnly
        ? 'Coverage check was pending'
        : 'Coverage check in progress'
      : result?.recovery?.state === 'indeterminate'
        ? 'Coverage check outcome unknown'
        : result?.payment_mode === 'cash' && result.cash_fallback
          ? 'Coverage unverified · cash pricing'
          : benefits?.service_date_matches === false
            ? 'Coverage returned · verify appointment date'
            : benefits?.active === true
              ? 'Coverage active'
              : benefits?.coverage_evidence?.status === 'EXPIRED'
                ? 'Returned policy expired'
                : benefits?.coverage_evidence?.status === 'FUTURE'
                  ? 'Coverage starts after this visit'
                  : benefits?.active === false
                    ? 'Coverage inactive'
                    : candidateStatus === 'EXPIRED'
                      ? 'Returned policy expired'
                      : candidateStatus === 'INACTIVE'
                        ? 'Returned policy inactive'
                        : candidateStatus === 'FUTURE'
                          ? 'Returned coverage starts after this visit'
                          : result?.coverage_status === 'NOT_FOUND'
                            ? 'Coverage not found electronically'
                            : result?.recovery?.state === 'rejected'
                              ? 'Coverage check could not complete'
                              : result?.coverage_status === 'REVIEW_REQUIRED'
                                ? 'Coverage unverified · review required'
                                : result?.ecw_coverage_active
                                  ? 'Policy in eCW · verify benefits'
                                  : result?.recovery?.reason_code === 'multiple_active_policies'
                                    ? 'Two active policies found'
                                    : failure?.kind === 'provider_enrollment'
                                      ? 'Clinic payer access missing'
                                      : result?.identity?.status === 'review'
                                        ? 'Policy details differ'
                                        : result?.cash_fallback
                                          ? 'Insurance amount unavailable'
                                          : result
                                            ? 'Payer check incomplete'
                                            : busy === 'estimate'
                                              ? 'Coverage check in progress'
                                              : 'Coverage not checked'
  const coverageSource =
    benefits?.active != null || benefits?.coverage_evidence
      ? result?.benefit_source
        ? `Payer eligibility via ${result.benefit_source}`
        : 'Payer eligibility source not retained'
      : result?.recovery?.state
        ? result.insurance_diagnosis?.coverageSource === 'stedi_discovery'
          ? 'Stedi coverage discovery · eligibility not confirmed'
          : 'Coverage discovery · eligibility not confirmed'
        : result?.ecw_coverage_active
          ? 'eCW policy record · payer benefits not confirmed'
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
    policyStatus === 'Coverage active'
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
  const networkConfirmed =
    benefits?.network_status === 'in_network' &&
    ['clinic_library_contract', 'payer_participation_message'].includes(
      benefits.network_source || '',
    )
  const networkLabel =
    benefits?.network_status === 'in_network'
      ? networkConfirmed
        ? 'In network'
        : 'In-network status unverified'
      : benefits?.network_status === 'out_of_network'
        ? 'Out of network'
        : contractCandidate
          ? 'Contract match unavailable'
          : 'Not determined'
  const networkSource =
    benefits?.network_source === 'clinic_library_contract'
      ? 'Current Clinical Library contract'
      : benefits?.network_source === 'clinic_library_absent'
        ? 'No current matching Clinical Library contract'
        : guidance?.network_source
  const networkTone =
    !result || tooEarly || result.recovery?.state === 'pending'
      ? ''
      : benefits?.network_status === 'in_network'
        ? networkConfirmed
          ? 'is-confirmed'
          : 'is-review'
        : benefits?.network_status === 'out_of_network'
          ? 'is-missing'
          : 'is-review'
  const unavailableReason = error
    ? 'Current benefits could not be loaded.'
    : failure?.detail || 'The payer did not return the patient share for the selected services.'
  const supportedAmount =
    guidance?.status !== 'deferred_billing' &&
    guidance?.amount_cents != null &&
    Number.isFinite(guidance.amount_cents) &&
    guidance.amount_cents >= 0
  const emg = result?.emg_option
  const billingInstruction = guidance?.billing_note?.kind === 'no_collection'
  const emgScenario = !billingInstruction && !cash && emg?.requested && !emg.included
  const cashAmount =
    cash && supportedAmount && guidance?.basis === 'practice_cash_price'
      ? guidance.range_min_cents != null && guidance.range_max_cents != null
        ? range(guidance.range_min_cents, guidance.range_max_cents)
        : money(guidance.amount_cents!)
      : cashPrice
  const amount = calculating
    ? null
    : cash
      ? cashAmount
      : supportedAmount
        ? money(guidance!.amount_cents!)
        : null
  const deferred =
    !calculating && !tooEarly && ((!cash && guidance?.status === 'deferred_billing') || !amount)
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
                : 'Review billing details'))
  const amountLabel = previousEstimate
    ? 'Previous estimate · needs recheck'
    : error
      ? 'Visit needs review'
      : result?.cash_fallback
        ? 'Self-pay option for this visit'
        : deferred && !cash
          ? 'Insurance advance today'
          : readOnly
            ? 'Suggested collection at this visit'
            : 'Collect at arrival'
  const amountParts = result?.encounter_summary?.components
  const officeDeductible = guidance?.services.find(
    (service) => service.code === officeCopayLine?.code,
  )?.deductible_applicability
  const cashExplanation = `${result?.cash_fallback?.reason ? `${result.cash_fallback.reason} ` : ''}${result?.eligibility_error === 'payer_routing_unresolved' && failure?.detail ? `${failure.detail} ` : ''}${result?.cash_quote?.message || 'The cash price is not established.'}`
  const baseExplanation = previousEstimate
    ? `The last saved result${result?.calculated_at ? ` from ${visitDate(result.calculated_at)} · ${visitTime(result.calculated_at)}` : ''} is shown. The update failed; reconfirm this visit before collecting.`
    : calculating
      ? 'Reading the visit, insurance and applicable benefits to calculate the amount.'
      : result?.payment_explanation?.sentence ||
        (readOnly && !result?.collection_guidance && !cash
          ? 'The original collection recommendation was not retained. The saved benefit and calculation details are shown below.'
          : deferred
            ? cash
              ? cashExplanation
              : error
                ? 'The appointment or current benefits could not be loaded. No insured collection amount is available.'
                : `${guidance?.reason || unavailableReason} Collect no insurance advance today; bill any patient responsibility after the claim.`
            : cash
              ? cashPrice && !error
                ? cashExplanation
                : 'The clinic cash price could not be calculated for this service plan.'
              : guidance?.reason || unavailableReason)
  const explanation =
    emgScenario && !calculating && !/nerve testing/i.test(baseExplanation)
      ? `${baseExplanation.replace(/\.$/, '')}; ${emg.total_min_cents != null ? 'the nerve-testing total below is for planning only' : 'nerve testing is excluded because its patient cost is unknown'}.`
      : baseExplanation
  const summaryExplanation = explanation
  const provider = result?.appointment_context?.rendering_provider?.name
  const anticipatedService =
    result?.price_range?.mode === 'alternatives'
      ? `${result.visit_type === 'new' ? 'New patient' : 'Established patient'} office consultation · E/M alternatives ${result.codes.map((row) => row.code).join(', ')}`
      : result?.service_plan?.recommendation_basis
  return {
    selected,
    benefits,
    price,
    officeCopayLine,
    copayEvidenceId,
    copaySource,
    failed,
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
