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
    patientBenefitsBound &&
    (result.fee_proxy_estimate.basis === 'bcbstx_uhc_in_network_fee_proxy'
      ? networkConfirmed
      : outOfNetworkConfirmed) &&
    !selectedCptIssue &&
    !servicePlanAction
      ? Math.max(
          0,
          result.fee_proxy_estimate.patient_share_cents -
            (result.encounter_summary?.payments_verified_cents || 0),
        )
      : null
  const provisionalAmount =
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
