import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'

export function publishedGehaZero(result: PrevisitEstimate) {
  return (
    result.collection_guidance?.basis === 'published_geha_pshb_zero_office' &&
    result.collection_guidance.amount_cents === 0
  )
}

export function calculatedShare(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
  automaticCashFallback: boolean,
) {
  return (
    !automaticCashFallback &&
    !model.previousEstimate &&
    !result.encounter_summary?.integrity_error &&
    result.price_range?.complete === true &&
    (result.encounter_summary?.complete === true || model.supportedAmount === true)
  )
}

export function visibleRateReference(result: PrevisitEstimate) {
  const temporaryRateReference =
    (/\baetna\b/i.test(result.payer || '') &&
      !result.temporary_rate_reference?.source?.startsWith('Aetna MEPPO')) ||
    (/\bhumana\b/i.test(result.payer || '') &&
      /blue cross/i.test(result.temporary_rate_reference?.source || ''))
      ? null
      : result.temporary_rate_reference
  return {
    temporaryRateReference,
    aetnaRateReference: Boolean(temporaryRateReference?.source?.startsWith('Aetna MEPPO')),
  }
}
