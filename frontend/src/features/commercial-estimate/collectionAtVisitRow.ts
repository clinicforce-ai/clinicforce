import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { feeProxyFormula } from './coinsuranceFormula'
import { money } from './format'

export function collectionAtVisitRow(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
  values: {
    combinedPlan: PrevisitEstimate['combined_emg_plan']
    feeProxy: PrevisitEstimate['fee_proxy_estimate']
    aetnaPlanning: PrevisitEstimate['aetna_diagnostic_reference'] | null
    aetnaCopayPlanning: number | null
    officeReferenceCopay: number | null
    automaticCashFallback: boolean
    cignaFeeProxy: boolean
    proxyFormula: ReturnType<typeof feeProxyFormula> | undefined
  },
): CostBreakdownRow {
  const {
    combinedPlan,
    feeProxy,
    aetnaPlanning,
    aetnaCopayPlanning,
    officeReferenceCopay,
    automaticCashFallback,
    cignaFeeProxy,
    proxyFormula,
  } = values
  const feeProxyCheckin =
    feeProxy && !cignaFeeProxy
      ? Math.max(
          0,
          feeProxy.patient_share_cents - (result.encounter_summary?.payments_verified_cents || 0),
        )
      : null
  const aetnaPaid = result.encounter_summary?.payments_verified_cents || 0
  const aetnaBalance = aetnaPlanning
    ? Math.max(0, aetnaPlanning.patient_share_cents - aetnaPaid)
    : null
  return {
    label:
      ((cignaFeeProxy && result.collection_guidance?.basis !== 'claim_after_service') ||
        feeProxyCheckin != null ||
        aetnaPlanning != null) &&
      !['bcbstx_proxy_advance', 'qmb_protection'].includes(result.collection_guidance?.basis || '')
        ? 'Planned amount to collect at visit'
        : 'Amount to collect at visit',
    value: combinedPlan
      ? `${money(combinedPlan.amount_cents)} planning estimate`
      : result.collection_guidance?.basis === 'qmb_protection' &&
          result.collection_guidance.amount_cents === 0
        ? money(0)
        : cignaFeeProxy &&
            result.collection_guidance?.basis === 'claim_after_service' &&
            result.collection_guidance.amount_cents === 0
          ? money(0)
          : !automaticCashFallback &&
              model.supportedAmount &&
              result.collection_guidance?.amount_cents != null
            ? money(result.collection_guidance.amount_cents)
            : cignaFeeProxy
              ? `${money(feeProxy!.patient_share_cents)} planning estimate`
              : feeProxyCheckin != null
                ? money(feeProxyCheckin)
                : aetnaPlanning
                  ? `${money(aetnaBalance!)} planning estimate`
                  : aetnaCopayPlanning != null
                    ? `${money(0)} now · verify before charging`
                    : officeReferenceCopay != null
                      ? `${money(officeReferenceCopay)} office-only reference`
                      : 'Not established',
    detail: combinedPlan
      ? `${money(combinedPlan.office_copay_cents)} + ${combinedPlan.coinsurance_percent}% × ${money(combinedPlan.diagnostic_allowed_cents)} = ${money(combinedPlan.patient_share_cents)}${combinedPlan.payments_cents ? `; less ${money(combinedPlan.payments_cents)} already paid` : ''}. Separate follow-up E/M is planned; deductible and out-of-pocket adjustments remain assumptions until payer balances are returned.`
      : result.collection_guidance?.basis === 'qmb_protection'
        ? 'QMB protection prohibits collecting Medicare cost sharing for covered care'
        : cignaFeeProxy && result.collection_guidance?.basis === 'claim_after_service'
          ? result.collection_guidance.reason
          : cignaFeeProxy
            ? `${proxyFormula?.total} ${feeProxy!.message || result.collection_guidance?.reason || ''}`
            : feeProxy != null
              ? result.collection_guidance?.basis === 'bcbstx_proxy_advance'
                ? `${proxyFormula?.total} Clinic-approved BCBS Texas fee proxy advance; final responsibility follows the UHC claim.`
                : `${proxyFormula?.total} Provisional BCBS Texas fee proxy; collect only after clinic policy and Medicare billing-protection clearance.`
              : aetnaPlanning
                ? `min((${money(aetnaPlanning.copay_cents / 2)} per test × 2) + ${money(aetnaPlanning.deductible_cents)} deductible + ${money(aetnaPlanning.coinsurance_cents)} coinsurance, ${money(aetnaPlanning.estimated_allowed_cents)} Aetna fee reference, ${money(aetnaPlanning.oop_remaining_cents)} remaining out-of-pocket) = ${money(aetnaPlanning.patient_share_cents)}${aetnaPaid ? `; less ${money(aetnaPaid)} already paid = ${money(aetnaBalance!)}` : ' before any prior payment'}. ${aetnaPlanning.message}`
                : officeReferenceCopay != null
                  ? 'Planning figure for one specialist office visit on the appointment date; not an authorized collection instruction. Other services, prior payments and Medicare cost-sharing protection are not included.'
                  : automaticCashFallback
                    ? 'Insurance collection is unresolved; any cash quote is listed separately'
                    : result.collection_guidance?.unconfirmed_zero_oop
                      ? 'Confirm the reported $0 out-of-pocket balance with the payer before collection'
                      : model.supportedAmount
                        ? result.collection_guidance?.basis === 'office_copay'
                          ? 'Office copay advance; other services may have a later balance'
                          : result.collection_guidance?.basis === 'out_of_pocket_limit'
                            ? 'Applicable out-of-pocket maximum has no remaining balance for covered care'
                            : result.collection_guidance?.basis === 'diagnostic_day_copay'
                              ? 'One diagnostic copay advance; other costs may follow the claim'
                              : result.collection_guidance?.basis === 'claim_after_service'
                                ? 'Clinic policy defers collection until after the claim'
                                : undefined
                        : undefined,
    emphasis: true,
  }
}
