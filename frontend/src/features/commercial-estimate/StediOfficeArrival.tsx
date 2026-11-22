import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { money } from './format'

export function isStediOfficeArrival(result: PrevisitEstimate, model: CommercialEstimateModel) {
  return (
    model.supportedAmount &&
    result.collection_guidance?.collection_rule?.mode === 'nmct_stedi_provisional_office_copay'
  )
}

export default function StediOfficeArrival({ result }: { result: PrevisitEstimate }) {
  return (
    <>
      <span>Amount to collect at visit · provisional office copay</span>
      <strong role="status">{money(result.collection_guidance!.amount_cents!)}</strong>
      <p>{result.collection_guidance!.reason}</p>
    </>
  )
}
