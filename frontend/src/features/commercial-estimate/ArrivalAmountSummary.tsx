import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { money } from './format'
import { aetnaOfficePlanningCopay, aetnaPendingOfficeCopay } from './payerReportedPartialCopay'
import { pendingCignaOfficeCopay } from './collectionAtVisitRow'

function nextStep(model: CommercialEstimateModel, result: PrevisitEstimate) {
  const lines = result.price_range?.mode === 'services' ? result.price_range.lines || [] : []
  if (!lines.length && result.service_plan?.method === 'unavailable')
    return {
      title: 'CPT suggestion unavailable',
      hint: 'Retry the coding suggestion for this visit.',
    }
  if (!lines.length && !result.selected_ids?.length)
    return { title: 'Select services', hint: 'Choose the planned CPTs for this visit.' }
  if (lines.some((line) => line.rate_resolution?.status !== 'RESOLVED'))
    return { title: 'Check CPT prices', hint: 'Match payer rates to the selected services.' }
  if (result.benefits?.active !== true)
    return { title: 'Check coverage', hint: 'Run eligibility for this visit.' }
  if (result.benefits.has_secondary || result.recovery?.reason_code === 'multiple_active_policies')
    return { title: 'Confirm payer order', hint: 'Resolve the active policies before collecting.' }
  if (model.guidance?.policy_setup_required)
    return {
      title: 'Approve collection policy',
      hint: 'The clinic must approve this payer route’s advance rule.',
    }
  if (model.requirementIssue)
    return {
      title: 'Review requirements',
      hint: 'Check referral and authorization for the selected CPTs.',
    }
  if (!/^in network|^out of network/i.test(model.networkLabel || ''))
    return { title: 'Verify network', hint: 'Confirm this plan and provider’s network tier.' }
  return { title: 'Review estimate', hint: 'See the missing item in the table below.' }
}

export default function ArrivalAmountSummary({
  model,
  result,
  partialCopay,
  cardPlanningCopay = null,
  referenceOnly = false,
  visibleAmount,
}: {
  model: CommercialEstimateModel
  result: PrevisitEstimate
  partialCopay: number | null
  cardPlanningCopay?: number | null
  referenceOnly?: boolean
  visibleAmount: boolean
}) {
  const action = nextStep(model, result)
  const plan = !model.cash && !model.previousEstimate ? result.combined_emg_plan : null
  const cignaPlanning =
    !model.cash &&
    !model.previousEstimate &&
    !['claim_after_service', 'qmb_protection'].includes(result.collection_guidance?.basis || '') &&
    result.fee_proxy_estimate?.basis === 'bcbstx_cigna_fee_proxy'
      ? result.fee_proxy_estimate
      : null
  const aetnaPlanning =
    !model.cash && !model.previousEstimate && result.collection_guidance?.amount_cents == null
      ? result.aetna_diagnostic_reference
      : null
  const diagnosticCopayReference =
    !model.cash &&
    !model.previousEstimate &&
    !model.calculating &&
    !model.requirementIssue &&
    !result.price_range?.service_issues?.length &&
    result.collection_guidance?.positive_advance_policy_missing === true
      ? result.collection_guidance.diagnostic_copay_reference_cents
      : null
  const botoxPlanning = !model.cash && !model.previousEstimate ? result.botox_preview : null
  const aetnaBalance = aetnaPlanning
    ? Math.max(
        0,
        aetnaPlanning.patient_share_cents -
          (result.encounter_summary?.payments_verified_cents || 0),
      )
    : null
  const officePlan = aetnaOfficePlanningCopay(
    result,
    model,
    result.price_range?.mode === 'services' ? result.price_range.lines || [] : [],
  )
  const officeBalance =
    officePlan == null
      ? null
      : Math.max(0, officePlan - (result.encounter_summary?.payments_verified_cents || 0))
  const pendingAetnaCopay = aetnaPendingOfficeCopay(result, model)
  const pendingCignaCopay = pendingCignaOfficeCopay(result, model)
  const needsAction = model.amountText === 'Insured amount unavailable'
  const additionalServicesPending = result.price_range?.lines?.some(
    (line) => !/^992(?:0[2-5]|1[2-5])$/.test(line.code),
  )
  return (
    <>
      <span>
        {plan
          ? 'Amount to collect at visit · planning estimate'
          : botoxPlanning
            ? 'Planned amount to collect · provisional CPT scenario'
            : cignaPlanning
              ? 'Cigna planning estimate · collection pending'
              : aetnaPlanning
                ? 'Planned amount to collect at visit'
                : diagnosticCopayReference != null
                  ? 'Diagnostic copay · collection pending'
                  : officeBalance != null
                    ? 'Planned amount to collect at visit'
                    : pendingAetnaCopay != null || pendingCignaCopay != null
                      ? 'Amount to collect at visit · pending verification'
                      : partialCopay != null
                        ? referenceOnly
                          ? 'Office-only reference · collection pending'
                          : 'Suggested amount to collect at visit'
                        : cardPlanningCopay != null
                          ? 'Card-based office suggestion · collection pending'
                          : needsAction
                            ? 'Next step'
                            : model.amountLabel}
      </span>
      <strong
        className={
          !visibleAmount &&
          !botoxPlanning &&
          !cignaPlanning &&
          !aetnaPlanning &&
          officeBalance == null &&
          pendingAetnaCopay == null &&
          pendingCignaCopay == null
            ? 'amount-pending'
            : undefined
        }
        role="status"
      >
        {plan
          ? money(plan.amount_cents)
          : botoxPlanning
            ? money(botoxPlanning.patient_share_cents)
            : cignaPlanning
              ? money(cignaPlanning.patient_share_cents)
              : aetnaPlanning
                ? money(aetnaBalance!)
                : diagnosticCopayReference != null
                  ? money(diagnosticCopayReference)
                  : officeBalance != null
                    ? money(officeBalance)
                    : pendingAetnaCopay != null
                      ? `Pending · ${money(pendingAetnaCopay)}`
                      : pendingCignaCopay != null
                        ? `Pending · ${money(pendingCignaCopay)}`
                      : partialCopay != null
                        ? money(partialCopay)
                        : cardPlanningCopay != null
                          ? money(cardPlanningCopay)
                          : needsAction
                            ? action.title
                            : model.amountText}
      </strong>
      {plan ? (
        <p>
          {money(plan.office_copay_cents)} office copay + {plan.coinsurance_percent}% ×{' '}
          {money(plan.diagnostic_allowed_cents)} diagnostic allowance ={' '}
          {money(plan.patient_share_cents)} before any recorded payment. Deductible and
          out-of-pocket balances were not returned.
        </p>
      ) : botoxPlanning ? (
        <p>
          {botoxPlanning.coinsurance_percent}% × {money(botoxPlanning.allowed_cents)} for one
          provisional CPT {botoxPlanning.code} procedure. Confirm the coding and member benefit
          limits before charging.
        </p>
      ) : cignaPlanning ? (
        <p>
          Conditional patient share using BCBS Texas fee references. Review the per-CPT formula
          below and obtain Cigna’s Explanation of Estimate before collection.
        </p>
      ) : aetnaPlanning ? (
        <p>
          {money(aetnaPlanning.copay_cents / 2)} per diagnostic test × 2 ={' '}
          {money(aetnaPlanning.patient_share_cents)} before payments
          {result.encounter_summary?.payments_verified_cents
            ? `; less ${money(result.encounter_summary.payments_verified_cents)} already paid = ${money(aetnaBalance!)}`
            : ''}
          . Verify the exact plan and Medicare billing protection before charging.
        </p>
      ) : diagnosticCopayReference != null ? (
        <p>
          One payer-reported diagnostic copay per day. Approve the clinic collection policy and
          check prior payments before charging.
        </p>
      ) : officeBalance != null ? (
        <p>
          {money(officePlan!)} specialist office copay + {money(0)} deductible + 0% coinsurance
          {result.encounter_summary?.payments_verified_cents
            ? `; less ${money(result.encounter_summary.payments_verified_cents)} already paid`
            : ''}{' '}
          = {money(officeBalance)} planning estimate. Confirm the final allowed charge and Medicare
          billing protection before collection.
        </p>
      ) : pendingAetnaCopay != null ? (
        <p>
          Stedi reported this specialist office copay. Confirm remaining deductible and
          out-of-pocket balances, conditional benefit terms, and prior payments before charging.
        </p>
      ) : pendingCignaCopay != null ? (
        <p>
          The reported office copay is conditional. Confirm it with the payer for this visit and
          CPT, the clinic collection policy, and any payments or credits before charging. Cigna’s
          allowed amount and full patient share remain unknown.
        </p>
      ) : partialCopay != null ? (
        <p>
          {referenceOnly
            ? 'Planning amount for this visit date; confirm payer date and Medicare cost-sharing protection before collection.'
            : additionalServicesPending
              ? 'Office copay reported; other service costs pending.'
              : 'Payer-reported office copay after recorded payments; separately performed services may change the total.'}
        </p>
      ) : cardPlanningCopay != null ? (
        <p>
          Printed specialist copay for one office visit. Payer benefits and separate imaging costs
          are unresolved; verify before charging.
        </p>
      ) : needsAction ? (
        <p>{action.hint}</p>
      ) : model.previousEstimate ? (
        <p>Recheck this saved visit before collecting.</p>
      ) : model.provisionalAmount != null || model.estimatedShare ? (
        <p>Planning estimate only. See the collection decision below.</p>
      ) : model.guidance?.basis === 'claim_after_service' && model.amount ? (
        <p>Claim first. Patient responsibility follows the processed claim.</p>
      ) : model.guidance?.basis === 'office_copay' && model.amount ? (
        <p>Office copay advance; other service costs may follow the claim.</p>
      ) : model.guidance?.basis === 'diagnostic_day_copay' && model.amount ? (
        <p>One diagnostic copay advance; deductible and final patient share follow the claim.</p>
      ) : null}
    </>
  )
}
