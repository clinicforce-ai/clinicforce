import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'

export const clinicFallbackReason =
  'No payment is requested at arrival under the clinic default. The current amount is unresolved; any lawful balance follows coverage, the processed claim, or applicable cash terms.'

export function isClinicZeroArrival(result: PrevisitEstimate, model: CommercialEstimateModel) {
  if (
    model.session?.arrival_collection_default_cents === 0 &&
    (model.previousEstimate || model.calculating || !model.supportedAmount)
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
