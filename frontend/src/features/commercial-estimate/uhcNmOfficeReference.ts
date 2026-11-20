import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'

const UHC_NM_0006_SOURCE = 'https://www.uhc.com/medicare/alphadog/AANM26LP0332760_000'

export function uhcNmCardPlan(result: PrevisitEstimate | null) {
  const card = result?.card_check
  return (
    (card?.published_inn_specialist_copay_cents == null ||
      card.published_inn_specialist_copay_cents === 5000) &&
    card?.published_oon_specialist_copay_cents === 8500 &&
    card?.published_oon_source_url === UHC_NM_0006_SOURCE
  )
}

export function uhcNmOfficeReference(result: PrevisitEstimate, model: CommercialEstimateModel) {
  const lines = result.price_range?.mode === 'services' ? result.price_range.lines || [] : []
  const line = lines.length === 1 ? lines[0] : null
  const paid = result.encounter_summary?.payments_verified_cents || 0
  const appointmentPos = result.appointment_context?.place_of_service
  const networkPos = result.network_evidence?.place_of_service
  const insuredAmount =
    result.collection_guidance?.amount_cents != null &&
    !(result.cash_fallback && result.collection_guidance?.basis === 'practice_cash_price')
  if (
    (model.cash && !result.cash_fallback) ||
    model.previousEstimate ||
    model.calculating ||
    insuredAmount ||
    result.billing_note?.kind === 'no_collection' ||
    result.telehealth ||
    (!appointmentPos && !networkPos) ||
    (appointmentPos != null && appointmentPos !== '11') ||
    (networkPos != null && networkPos !== '11') ||
    result.billing_protection?.status === 'qmb' ||
    result.benefits?.qmb_status === 'protected' ||
    result.benefits?.copay_cents != null ||
    result.price_range?.service_issues?.length ||
    !line ||
    line.code !== '99213' ||
    line.units !== 1 ||
    line.modifiers?.length ||
    !uhcNmCardPlan(result) ||
    !Number.isInteger(paid) ||
    paid < 0
  )
    return null
  return { inNetworkCopay: 5000, outOfNetworkCopay: 8500, paid, source: UHC_NM_0006_SOURCE }
}
