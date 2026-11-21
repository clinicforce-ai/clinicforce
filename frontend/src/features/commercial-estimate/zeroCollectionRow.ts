import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { money } from './format'

export function zeroCollectionRow(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
): CostBreakdownRow | null {
  const guidance = result.collection_guidance
  const rule = guidance?.collection_rule?.mode
  if (
    guidance?.amount_cents !== 0 ||
    !(
      rule === 'aetna_office_individual_oop_deferral' ||
      (model.supportedAmount &&
        (rule === 'individual_oop_zero_deferral' ||
          rule === 'bcbstx_botox_family_oop_deferral' ||
          rule === 'bcbstx_ppo_televisit_deferral' ||
          rule === 'uhc_choice_plus_family_oop_deferral'))
    )
  )
    return null
  return {
    label: 'Amount to collect at visit',
    value: money(0),
    detail: guidance.reason,
    emphasis: true,
  }
}
