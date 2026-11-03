import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { money } from './format'

export function cignaCardReference(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
  benefits: PrevisitEstimate['benefits'],
) {
  return !model.cash &&
    !model.previousEstimate &&
    !model.calculating &&
    !model.supportedAmount &&
    result.collection_guidance?.amount_cents == null &&
    /\bcigna\b/i.test(result.payer || '') &&
    result.card_check?.status !== 'conflict' &&
    !result.card_check?.conflicts?.length &&
    !result.card_check?.differences?.length &&
    benefits?.active === true &&
    benefits.network_status === 'in_network' &&
    !benefits.coverage_status_conflict &&
    !benefits.network_conflict &&
    !benefits.has_secondary &&
    result.identity?.status !== 'review' &&
    benefits.oop_remaining_cents !== 0 &&
    !result.price_range?.service_issues?.length &&
    !['multiple_active_policies', 'same_policy_evidence_conflict'].includes(
      result.recovery?.reason_code || '',
    ) &&
    result.billing_protection?.status !== 'qmb'
    ? result.card_check?.cigna_card_reference
    : null
}

export function cignaCardRows(
  card: NonNullable<PrevisitEstimate['card_check']>['cigna_card_reference'] | null | undefined,
  feeProxy: boolean,
): CostBreakdownRow[] {
  return card && feeProxy
    ? [
        {
          label: 'Insurance card specialist office copays',
          value: `${money(card.tier1_specialist_copay_cents)} Tier 1; ${money(card.nontier1_specialist_copay_cents)} non-Tier-1`,
          detail:
            'Card-printed office terms only; diagnostic CPT applicability and provider tier require verification.',
        },
        {
          label: 'Insurance card in-network split',
          value: `${card.in_network_coinsurance_split} printed card reference`,
          detail: 'The exact CPT response and Cigna allowance determine the diagnostic share.',
        },
      ]
    : []
}
