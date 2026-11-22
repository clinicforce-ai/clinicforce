import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { networkSourceLabel } from './networkSourceLabel'

export function isStediOfficeArrival(result: PrevisitEstimate, model: CommercialEstimateModel) {
  return (
    model.supportedAmount &&
    result.collection_guidance?.collection_rule?.mode === 'nmct_stedi_provisional_office_copay'
  )
}

export function arrivalNetworkSource(result: PrevisitEstimate | null | undefined) {
  const evidence = result?.network_evidence
  return evidence?.match_kind === 'shared_product_word'
    ? `NMCT product roster · shared plan word: ${(evidence.matched_words || []).join(', ')} · not an exact product or payer confirmation`
    : networkSourceLabel(
        evidence?.decision,
        result?.benefits?.network_source,
        result?.collection_guidance?.network_source,
      )
}

export function stediOfficeAdvance(result: PrevisitEstimate | null | undefined) {
  const guidance = result?.collection_guidance
  return (
    guidance?.collection_rule?.mode === 'nmct_stedi_provisional_office_copay' &&
    guidance.payer_response_date != null &&
    result?.encounter_summary?.collect_today_cents === guidance.amount_cents
  )
}
