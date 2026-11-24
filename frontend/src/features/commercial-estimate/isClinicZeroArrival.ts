import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'

export const clinicFallbackReason =
  'The visit collection amount is pending review of the payer and service details.'

export function isClinicZeroArrival(result: PrevisitEstimate, model: CommercialEstimateModel) {
  if (result.eligibility_request_state === 'pending') return false
  if (
    result.collection_guidance?.potential_office_collection &&
    !model.previousEstimate &&
    !model.calculating
  )
    return true
  return (
    !model.previousEstimate &&
    !model.calculating &&
    model.supportedAmount &&
    result.collection_guidance?.collection_rule?.mode === 'clinic_zero_arrival_default' &&
    result.collection_guidance.amount_cents === 0
  )
}
