import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'

export function isClinicZeroArrival(result: PrevisitEstimate, model: CommercialEstimateModel) {
  return (
    !model.cash &&
    !model.previousEstimate &&
    !model.calculating &&
    model.supportedAmount &&
    result.collection_guidance?.collection_rule?.mode === 'clinic_zero_arrival_default' &&
    result.collection_guidance.amount_cents === 0
  )
}
