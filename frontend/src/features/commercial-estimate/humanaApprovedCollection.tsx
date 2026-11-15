import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { money } from './format'
import {
  pendingHumanaSpecialistCopay,
  supportedHumanaSpecialistAdvance,
} from './commercialCostState'

export function humanaApprovedCollection(result: PrevisitEstimate, model: CommercialEstimateModel) {
  const guidance = result.collection_guidance
  if (supportedHumanaSpecialistAdvance(result, model)) {
    return (
      <>
        <span>Amount approved to collect at visit</span>
        <strong role="status">{money(guidance!.amount_cents!)}</strong>
        <p>
          Refundable advance from Stedi’s specialist office copay, less recorded payments. Final
          responsibility follows the processed claim and member billing protections.
        </p>
      </>
    )
  }
  const copay = pendingHumanaSpecialistCopay(result, model)
  if (copay == null) return null
  return (
    <>
      <span>Amount approved to collect at visit</span>
      <strong role="status">{money(0)}</strong>
      <p>
        Stedi reports a {money(copay)} specialist office copay. No positive advance is approved from
        this record. Final responsibility follows the processed claim.
      </p>
    </>
  )
}
