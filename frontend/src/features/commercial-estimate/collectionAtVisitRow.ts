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
    partialOfficeCopay: number | null
    cardPlanningCopay: number | null
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
    partialOfficeCopay,
    cardPlanningCopay,
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
  const officeBalance =
    aetnaCopayPlanning == null ? null : Math.max(0, aetnaCopayPlanning - aetnaPaid)
  const botoxPreview = !model.cash && !model.previousEstimate ? result.botox_preview : null
  const officeLine =
    result.price_range?.mode === 'services' && result.price_range.lines?.length === 1
      ? result.price_range.lines[0]
      : null
  const pendingCignaCopay =
    !model.cash &&
    !model.previousEstimate &&
    !model.requirementIssue &&
    result.collection_guidance?.amount_cents == null &&
    /\bcigna\b/i.test(result.payer || '') &&
    result.benefits?.active !== false &&
    result.benefits?.network_status === 'in_network' &&
    !result.benefits?.network_conflict &&
    result.benefits?.service_date_matches !== false &&
    !result.benefits?.coverage_status_conflict &&
    !result.benefits?.coverage_dates_invalid &&
    !result.benefits?.has_secondary &&
    !result.benefits?.source_errors?.length &&
    !result.benefits?.historical_only &&
    !result.price_range?.service_issues?.length &&
    result.recovery?.reason_code !== 'multiple_active_policies' &&
    result.identity?.status !== 'review' &&
    result.billing_protection?.status !== 'qmb' &&
    officeLine?.units === 1 &&
    /^992(?:0[2-5]|1[2-5])$/.test(officeLine.code) &&
    officeLine.benefits?.active !== false &&
    officeLine.benefits?.network_status !== 'out_of_network' &&
    !officeLine.benefits?.coverage_status_conflict &&
    !officeLine.benefits?.coverage_dates_invalid &&
    officeLine.benefits?.service_date_matches !== false &&
    !officeLine.benefits?.source_errors?.length &&
    (officeLine.benefits?.deductible_applies !== true ||
      officeLine.benefits?.deductible_remaining_cents === 0) &&
    [undefined, null, '0', '0.0', '0.00'].includes(officeLine.benefits?.coinsurance_percent) &&
    (officeLine.benefits?.oop_remaining_cents == null ||
      officeLine.benefits.oop_remaining_cents >= officeLine.benefits.copay_cents!) &&
    Number.isInteger(officeLine.benefits?.copay_cents) &&
    officeLine.benefits!.copay_cents! > 0
      ? Math.max(
          0,
          officeLine.benefits.copay_cents! -
            Math.max(0, result.encounter_summary?.payments_verified_cents || 0),
        )
      : null
  const diagnosticCopayReference =
    !model.cash &&
    !model.previousEstimate &&
    !model.calculating &&
    !model.requirementIssue &&
    !result.price_range?.service_issues?.length &&
    result.collection_guidance?.amount_cents == null &&
    result.collection_guidance?.positive_advance_policy_missing === true &&
    result.collection_guidance.diagnostic_copay_reference_cents != null
      ? result.collection_guidance.diagnostic_copay_reference_cents
      : null
  return {
    label: botoxPreview
      ? 'Planned amount to collect at visit'
      : diagnosticCopayReference != null
        ? 'Diagnostic copay · collection pending'
        : (partialOfficeCopay != null || cardPlanningCopay != null) && feeProxyCheckin == null
          ? 'Suggested amount to collect at visit'
          : ((cignaFeeProxy && result.collection_guidance?.basis !== 'claim_after_service') ||
                feeProxyCheckin != null ||
                aetnaPlanning != null ||
                aetnaCopayPlanning != null) &&
              !['bcbstx_proxy_advance', 'qmb_protection'].includes(
                result.collection_guidance?.basis || '',
              )
            ? 'Planned amount to collect at visit'
            : 'Amount to collect at visit',
    value: combinedPlan
      ? `${money(combinedPlan.amount_cents)} planning estimate`
      : result.collection_guidance?.basis === 'qmb_protection' &&
          result.collection_guidance.amount_cents === 0
        ? money(0)
        : botoxPreview
          ? `${money(botoxPreview.patient_share_cents)} provisional · confirm CPT`
          : cignaFeeProxy &&
              result.collection_guidance?.basis === 'claim_after_service' &&
              result.collection_guidance.amount_cents === 0
            ? money(0)
            : !automaticCashFallback &&
                model.supportedAmount &&
                result.collection_guidance?.amount_cents != null
              ? money(result.collection_guidance.amount_cents)
              : diagnosticCopayReference != null
                ? `${money(diagnosticCopayReference)} once per day · verify before charging`
                : cignaFeeProxy
                  ? `${money(feeProxy!.patient_share_cents)} planning estimate`
                  : feeProxyCheckin != null
                    ? money(feeProxyCheckin)
                    : aetnaPlanning
                      ? `${money(aetnaBalance!)} planning estimate`
                      : aetnaCopayPlanning != null
                        ? `${money(officeBalance!)} planning estimate`
                        : officeReferenceCopay != null
                          ? `${money(officeReferenceCopay)} office-only reference`
                          : partialOfficeCopay != null
                            ? money(partialOfficeCopay)
                            : cardPlanningCopay != null
                              ? `${money(cardPlanningCopay)} office-only card suggestion`
                              : pendingCignaCopay != null
                                ? `Pending · ${money(pendingCignaCopay)} office copay if verified`
                                : 'Not established',
    detail: combinedPlan
      ? `${money(combinedPlan.office_copay_cents)} + ${combinedPlan.coinsurance_percent}% × ${money(combinedPlan.diagnostic_allowed_cents)} = ${money(combinedPlan.patient_share_cents)}${combinedPlan.payments_cents ? `; less ${money(combinedPlan.payments_cents)} already paid` : ''}. Separate follow-up E/M is planned; deductible and out-of-pocket adjustments remain assumptions until payer balances are returned.`
      : result.collection_guidance?.basis === 'qmb_protection'
        ? 'QMB protection prohibits collecting Medicare cost sharing for covered care'
        : botoxPreview
          ? `${botoxPreview.coinsurance_percent}% × ${money(botoxPreview.allowed_cents)} for CPT ${botoxPreview.code} = ${money(botoxPreview.patient_share_cents)}. Confirm the injection site, individual limits, and prior payments before charging.`
          : cignaFeeProxy && result.collection_guidance?.basis === 'claim_after_service'
            ? result.collection_guidance.reason
            : diagnosticCopayReference != null
              ? 'Payer-reported diagnostic copay for the selected testing. Approve the clinic collection policy and account for prior payments before charging; the full patient share remains unresolved.'
              : cignaFeeProxy
                ? `${proxyFormula?.total} ${feeProxy!.message || result.collection_guidance?.reason || ''}`
                : feeProxy != null
                  ? result.collection_guidance?.basis === 'bcbstx_proxy_advance'
                    ? `${proxyFormula?.total} Clinic-approved BCBS Texas fee proxy advance; final responsibility follows the UHC claim.`
                    : `${proxyFormula?.total} Provisional BCBS Texas fee proxy; collect only after clinic policy and Medicare billing-protection clearance.`
                  : aetnaPlanning
                    ? `min((${money(aetnaPlanning.copay_cents / 2)} per test × 2) + ${money(aetnaPlanning.deductible_cents)} deductible + ${money(aetnaPlanning.coinsurance_cents)} coinsurance, ${money(aetnaPlanning.estimated_allowed_cents)} Aetna fee reference, ${money(aetnaPlanning.oop_remaining_cents)} remaining out-of-pocket) = ${money(aetnaPlanning.patient_share_cents)}${aetnaPaid ? `; less ${money(aetnaPaid)} already paid = ${money(aetnaBalance!)}` : ' before any prior payment'}. ${aetnaPlanning.message}`
                    : aetnaCopayPlanning != null
                      ? `${money(aetnaCopayPlanning)} specialist office copay + ${money(0)} deductible + 0% coinsurance = ${money(aetnaCopayPlanning)}${aetnaPaid ? `; less ${money(aetnaPaid)} already paid = ${money(officeBalance!)}` : ' before any prior payment'}. Planning amount; confirm the final allowed charge and Medicare billing protection before collection.`
                      : officeReferenceCopay != null
                        ? 'Planning figure for one specialist office visit on the appointment date; not an authorized collection instruction. Other services, prior payments and Medicare cost-sharing protection are not included.'
                        : partialOfficeCopay != null
                          ? 'Suggested office visit copay after recorded payments. Separately performed DBS device services may add cost.'
                          : cardPlanningCopay != null
                            ? 'Printed specialist copay for one office visit; verify before charging. Planned imaging and final payer share are unresolved.'
                            : pendingCignaCopay != null
                              ? 'Complete the visit-date payer check, confirm this copay and the clinic collection policy, and account for verified payments before charging. Full patient share remains unresolved.'
                              : automaticCashFallback
                                ? 'Insurance collection is unresolved; any cash quote is listed separately'
                                : result.collection_guidance?.unconfirmed_zero_oop
                                  ? 'Confirm the reported $0 out-of-pocket balance with the payer before collection'
                                  : model.supportedAmount
                                    ? result.collection_guidance?.basis === 'office_copay'
                                      ? 'Office copay advance; other services may have a later balance'
                                      : result.collection_guidance?.basis === 'out_of_pocket_limit'
                                        ? 'Applicable out-of-pocket maximum has no remaining balance for covered care'
                                        : result.collection_guidance?.basis ===
                                            'diagnostic_day_copay'
                                          ? 'One diagnostic copay advance; other costs may follow the claim'
                                          : result.collection_guidance?.basis ===
                                              'claim_after_service'
                                            ? 'Clinic policy defers collection until after the claim'
                                            : undefined
                                    : undefined,
    emphasis: true,
  }
}
