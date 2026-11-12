import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { money } from './format'

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

export function supportedOfficeCopayShare(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
) {
  const guidance = result.collection_guidance
  return model.supportedAmount &&
    guidance?.basis === 'office_copay' &&
    guidance.amount_cents != null
    ? `${money(guidance.amount_cents)} office copay advance; final share pending`
    : null
}

export function visibleRateReference(result: PrevisitEstimate) {
  const temporaryRateReference =
    /\bhumana\b/i.test(result.payer || '') &&
    /blue cross/i.test(result.temporary_rate_reference?.source || '')
      ? null
      : result.temporary_rate_reference
  return {
    temporaryRateReference,
    aetnaRateReference: Boolean(temporaryRateReference?.source?.startsWith('Aetna MEPPO')),
  }
}

export function applyBluePpoFamilyQuote(rows: CostBreakdownRow[], result: PrevisitEstimate) {
  const guidance = result.collection_guidance
  const mode = guidance?.collection_rule?.mode
  if (!guidance || !['blue_ppo_family_deductible_quote', 'cigna_ppo_deductible_quote'].includes(mode || '')) return
  const share = rows.find((row) => row.label.endsWith('patient share'))
  if (!share) return
  share.value = `${money(guidance.reference_total_cents!)} provisional · ${mode === 'cigna_ppo_deductible_quote' ? 'office deductible assumed' : 'family deductible assumed'}`
  share.detail = guidance.reason
}
