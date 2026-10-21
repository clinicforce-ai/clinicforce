import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { money } from './format'

export function overviewExplanation(
  model: CommercialEstimateModel,
  result: NonNullable<CommercialEstimateModel['result']>,
  fullBreakdown: boolean,
): string {
  if (model.previousEstimate || model.calculating) return model.summaryExplanation
  if (!model.amount && model.summaryExplanation !== result.payment_explanation?.sentence)
    return model.summaryExplanation
  if (result.payment_explanation?.sentence) return result.payment_explanation.sentence
  if (fullBreakdown && result.benefits?.network_source !== 'clinic_contract_inventory') {
    const plan = model.planName || result.payer || 'the medical plan'
    const parts = model.amountParts
    const share = [
      parts?.copay_cents ? `${money(parts.copay_cents)} copay` : null,
      parts?.deductible_cents ? `${money(parts.deductible_cents)} deductible` : null,
      parts?.coinsurance_cents ? `${money(parts.coinsurance_cents)} coinsurance` : null,
    ]
      .filter(Boolean)
      .join(' and ')
    if (result.benefits?.network_status === 'in_network') {
      return `Collect ${model.amountText} now for ${plan} using in-network contracted CPT prices with ${share || 'the supported cost sharing'}.`
    }
    if (result.benefits?.network_status === 'out_of_network') {
      const cms = result.price_range?.lines?.some(
        (line) => line.allowed_basis === 'cms_original_medicare_estimate',
      )
      return `Collect ${model.amountText} now for ${plan} using ${cms ? 'the CMS Original Medicare benchmark estimate' : 'payer out-of-network CPT prices'} with ${share || 'the supported cost sharing'}.`
    }
    return 'Verify the medical plan network and selected CPT prices before collecting this saved advance.'
  }
  if (
    model.amount &&
    result.payment_explanation?.reason_code === 'insured_services' &&
    !fullBreakdown &&
    !result.payment_explanation.sentence
  ) {
    return 'The saved advance lacks a complete itemized explanation; verify the CPT calculation before collecting.'
  }
  return model.summaryExplanation
}
