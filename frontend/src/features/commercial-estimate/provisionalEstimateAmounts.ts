import type { PrevisitEstimate } from '../../services/api/commercial-estimator'

export function provisionalEstimateAmounts({
  result,
  cash,
  previousEstimate,
  calculating,
  supportedAmount,
  patientBenefitsBound,
  networkConfirmed,
  outOfNetworkConfirmed,
  selectedCptIssue,
  requirementIssue,
  servicePlanAction,
  readOnly,
  currentPayerAmount,
}: {
  result: PrevisitEstimate | null
  cash: boolean
  previousEstimate: boolean
  calculating: boolean
  supportedAmount: boolean
  patientBenefitsBound: boolean
  networkConfirmed: boolean
  outOfNetworkConfirmed: boolean
  selectedCptIssue: boolean
  requirementIssue: boolean
  servicePlanAction: boolean
  readOnly: boolean
  currentPayerAmount: boolean
}) {
  const price = result?.price_range
  const feeProxyAmount =
    !cash &&
    !previousEstimate &&
    !calculating &&
    !supportedAmount &&
    result?.collection_guidance?.basis !== 'qmb_protection' &&
    result?.fee_proxy_estimate &&
    result.fee_proxy_estimate.basis !== 'bcbstx_uhc_dsnp_office_proxy' &&
    patientBenefitsBound &&
    (['bcbstx_uhc_in_network_fee_proxy', 'bcbstx_uhc_choice_plus_fee_proxy'].includes(
      result.fee_proxy_estimate.basis,
    )
      ? networkConfirmed
      : outOfNetworkConfirmed) &&
    !selectedCptIssue &&
    (result.fee_proxy_estimate.basis !== 'bcbstx_uhc_choice_plus_fee_proxy' || !requirementIssue) &&
    !servicePlanAction
      ? Math.max(
          0,
          result.fee_proxy_estimate.patient_share_cents -
            (result.encounter_summary?.payments_verified_cents || 0),
        )
      : null
  const provisionalAmount =
    (!cash &&
    !previousEstimate &&
    !calculating &&
    !supportedAmount &&
    result?.aetna_office_planning?.status === 'planning_only' &&
    result.aetna_office_planning.collectable === false
      ? Math.max(
          0,
          result.aetna_office_planning.deductible_applies_cents -
            (result.encounter_summary?.payments_verified_cents || 0),
        )
      : null) ??
    feeProxyAmount ??
    (!readOnly &&
    !cash &&
    !previousEstimate &&
    !calculating &&
    !supportedAmount &&
    result?.service_selection_status === 'preview' &&
    (!!result.selected_ids?.length || result.provisional_scenario?.count === 1) &&
    currentPayerAmount &&
    !result.encounter_summary?.integrity_error &&
    (price?.mode === 'services' || price?.mode === 'alternatives') &&
    price.complete &&
    price.patient_min_cents != null &&
    price.patient_max_cents != null &&
    price.patient_min_cents === result.planning_estimate?.min_cents &&
    price.patient_max_cents === result.planning_estimate?.max_cents
      ? price.patient_min_cents
      : null)
  return { feeProxyAmount, provisionalAmount }
}

export function provisionalSummaryExplanation(
  result: PrevisitEstimate | null,
  provisionalAmount: number | null,
  explanation: string,
): string {
  if (provisionalAmount == null) return explanation
  if (result?.aetna_office_planning?.copay_cents != null)
    return 'The payer-reported specialist copay is the estimated patient share. The approved Aetna fee less that copay is the estimated plan share. Visit-date coverage, Medicare billing protection and prior payments still control collection.'
  if (result?.aetna_office_planning)
    return 'Deductible-applies planning amount from the approved Aetna office fee and prior-date payer terms. Confirm visit-date coverage, provider network, deductible applicability, other coverage and prior payments before setting a collection amount.'
  if (result?.provisional_scenario?.kind === 'ncs_study_count')
    return 'Estimated patient share across alternative NCS study counts. Muscle testing is excluded unless documented. Do not collect this planning range.'
  return 'Estimated patient share for the provisional CPT scenario. Review the services and requirements before collecting.'
}
