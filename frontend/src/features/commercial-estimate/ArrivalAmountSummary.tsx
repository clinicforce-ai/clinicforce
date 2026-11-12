import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { money } from './format'
import {
  aetnaOfficePlanningCopay,
  aetnaPendingOfficeCopay,
  aetnaPriorDateReferenceCopay,
} from './payerReportedPartialCopay'
import { pendingBluecardOfficeCopay, pendingCignaOfficeCopay } from './collectionAtVisitRow'
import { bluecardOfficeDeductibleScenario } from './bluecardOfficeDeductibleScenario'

function nextStep(model: CommercialEstimateModel, result: PrevisitEstimate) {
  const lines = result.price_range?.mode === 'services' ? result.price_range.lines || [] : []
  if (result.benefits?.active !== true)
    return {
      title: 'Check coverage',
      hint: 'Run eligibility for this visit before setting a collection amount.',
    }
  if (!lines.length && result.service_plan?.method === 'unavailable')
    return {
      title: 'CPT suggestion unavailable',
      hint: 'Retry the coding suggestion for this visit.',
    }
  if (!lines.length && !result.selected_ids?.length)
    return { title: 'Select services', hint: 'Choose the planned CPTs for this visit.' }
  if (
    (result.benefits.has_secondary ||
      result.recovery?.reason_code === 'multiple_active_policies') &&
    result.benefits.primary_secondary_order?.status !== 'verified'
  )
    return { title: 'Confirm payer order', hint: 'Resolve the active policies before collecting.' }
  if (result.benefits.source_errors?.length)
    return {
      title: 'Check payer response',
      hint: 'Review the incomplete benefit check for this visit.',
    }
  const cignaTerms = [result.benefits, ...lines.map((line) => line.benefits)]
  if (
    /^Cigna$/i.test(result.payer || '') &&
    result.collection_guidance?.amount_cents == null &&
    cignaTerms.every(
      (term) =>
        term?.copay_cents == null &&
        term?.deductible_annual_cents == null &&
        term?.deductible_remaining_cents == null &&
        term?.coinsurance_percent == null &&
        term?.oop_remaining_cents == null,
    )
  )
    return {
      title: 'Check Cigna plan benefits',
      hint:
        result.appointment_context?.place_of_service === '11'
          ? 'Request visit-date office cost sharing and provider participation from the plan administrator.'
          : 'Request visit-date cost sharing and provider participation from the plan administrator.',
    }
  if (!/^in network|^out of network/i.test(model.networkLabel || ''))
    return { title: 'Verify network', hint: 'Confirm this plan and provider’s network tier.' }
  if (model.requirementIssue)
    return {
      title: 'Review requirements',
      hint: 'Check referral and authorization for the selected CPTs.',
    }
  if (
    result.collection_guidance?.positive_advance_policy_missing ||
    result.collection_guidance?.published_copay_policy_missing
  )
    return {
      title: 'Approve collection policy',
      hint: 'The clinic must approve this payer route’s advance rule.',
    }
  if (
    result.benefit_source?.includes('plan administrator') &&
    lines.length === 1 &&
    /^992(?:0[2-5]|1[2-5])$/.test(lines[0].code) &&
    lines[0].benefits?.copay_cents == null
  )
    return {
      title: 'Review office copay',
      hint: 'Confirm the diagnosis and applicable specialist copay.',
    }
  if (lines.some((line) => line.rate_resolution?.status !== 'RESOLVED'))
    return { title: 'Check CPT prices', hint: 'Match payer rates to the selected services.' }
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
  if (
    !model.cash &&
    !model.previousEstimate &&
    !model.calculating &&
    ['cms_msp_part_b_deferral', 'aetna_choice_family_oop_deferral'].includes(
      result.collection_guidance?.collection_rule?.mode || '',
    )
  )
    return (
      <>
        <span>Amount to collect at visit</span>
        <strong role="status">{money(0)}</strong>
        <p>{result.collection_guidance?.reason}</p>
      </>
    )
  const action = nextStep(model, result)
  const plan = !model.cash && !model.previousEstimate ? result.combined_emg_plan : null
  const biopsyTarget =
    !model.cash && !model.previousEstimate && !model.calculating
      ? result.provisional_collection_target
      : null
  const biopsyBalance = biopsyTarget
    ? Math.max(
        0,
        biopsyTarget.amount_cents - (result.encounter_summary?.payments_verified_cents || 0),
      )
    : null
  const biopsyOfficePending = !!biopsyTarget?.pending_codes?.length
  const cignaPlanning =
    !model.cash &&
    !model.previousEstimate &&
    !model.supportedAmount &&
    !['claim_after_service', 'qmb_protection'].includes(result.collection_guidance?.basis || '') &&
    result.fee_proxy_estimate?.basis === 'bcbstx_cigna_fee_proxy'
      ? result.fee_proxy_estimate
      : null
  const dsnpOffice =
    !model.cash &&
    !model.previousEstimate &&
    !model.calculating &&
    result.fee_proxy_estimate?.basis === 'bcbstx_uhc_dsnp_office_proxy'
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
  const officeTerms =
    result.price_range?.mode === 'services' && result.price_range.lines?.length === 1
      ? result.price_range.lines[0].benefits
      : null
  const separateOfficeCopayRemaining = officeTerms?.separate_copay_limit
    ? officeTerms.copay_remaining_cents
    : null
  const officePolicyPending =
    officePlan != null && result.collection_guidance?.positive_advance_policy_missing === true
  const pendingAetnaCopay = aetnaPendingOfficeCopay(result, model)
  const pendingBluecardCopay = pendingBluecardOfficeCopay(result, model)
  const priorDateAetnaCopay = aetnaPriorDateReferenceCopay(result, model)
  const priorDateAetnaBalance =
    priorDateAetnaCopay == null
      ? null
      : Math.max(0, priorDateAetnaCopay - (result.encounter_summary?.payments_verified_cents || 0))
  const aetnaMedicareScenario = /^medicare\b/i.test(
    (result.reference_benefits ?? result.benefits)?.reported_secondary_payer?.payer ||
      result.benefits?.primary_secondary_order?.secondary_payer ||
      '',
  )
  const pendingCignaCopay = pendingCignaOfficeCopay(result, model)
  const pendingUhcHmoPosCopay =
    partialCopay != null &&
    /\bhmopos\b/i.test(result.plan_details?.stedi?.names?.[0] || '') &&
    result.billing_protection?.status !== 'not_qmb'
  const bluecardDeductible =
    !model.cash && !model.previousEstimate ? bluecardOfficeDeductibleScenario(result) : null
  const bluecardDeposit =
    bluecardDeductible &&
    result.collection_guidance?.collection_rule?.mode ===
      'bluecard_family_office_refundable_deposit' &&
    model.supportedAmount
  const needsAction = model.amountText === 'Insured amount unavailable'
  const additionalServicesPending = result.price_range?.lines?.some(
    (line) => !/^992(?:0[2-5]|1[2-5])$/.test(line.code),
  )
  return (
    <>
      <span>
        {dsnpOffice
          ? 'Amount to collect at visit · dual status unresolved'
          : plan
            ? 'Amount to collect at visit · planning estimate'
            : biopsyBalance != null
              ? biopsyOfficePending
                ? 'Provisional biopsy target · office share pending'
                : 'Provisional collection target · confirmation pending'
              : botoxPlanning
                ? 'Planned amount to collect · provisional CPT scenario'
                : cignaPlanning
                  ? 'Cigna planning estimate · collection pending'
                  : bluecardDeductible
                    ? bluecardDeposit
                      ? 'Refundable deposit to collect at visit'
                      : 'Amount to collect at visit · provisional target'
                    : aetnaPlanning
                      ? 'Planned amount to collect at visit'
                      : diagnosticCopayReference != null
                        ? 'Diagnostic copay · collection pending'
                        : officeBalance != null
                          ? officePolicyPending
                            ? 'Amount to collect at visit · pending policy approval'
                            : 'Planned amount to collect at visit'
                          : pendingAetnaCopay != null ||
                              pendingCignaCopay != null ||
                              pendingBluecardCopay != null
                            ? 'Amount to collect at visit · pending verification'
                            : partialCopay != null
                              ? pendingUhcHmoPosCopay
                                ? 'Amount to collect at visit · verify Medicare protection'
                                : referenceOnly
                                  ? 'Office-only reference · collection pending'
                                  : 'Suggested amount to collect at visit'
                              : cardPlanningCopay != null
                                ? 'Card-based office suggestion · collection pending'
                                : priorDateAetnaCopay != null
                                  ? aetnaMedicareScenario
                                    ? 'Aetna office copay · Medicare review'
                                    : 'Payer-reported office copay'
                                  : needsAction
                                    ? 'Next step'
                                    : model.amountLabel}
      </span>
      <strong
        className={
          !visibleAmount &&
          !dsnpOffice &&
          biopsyBalance == null &&
          !botoxPlanning &&
          !cignaPlanning &&
          !bluecardDeductible &&
          !aetnaPlanning &&
          officeBalance == null &&
          pendingAetnaCopay == null &&
          pendingBluecardCopay == null &&
          pendingCignaCopay == null &&
          priorDateAetnaCopay == null
            ? 'amount-pending'
            : undefined
        }
        role="status"
      >
        {dsnpOffice
          ? `${money(0)} at check-in`
          : plan
            ? money(plan.amount_cents)
            : biopsyBalance != null
              ? `${money(biopsyBalance)}${biopsyOfficePending ? ' biopsy only' : ''}`
              : botoxPlanning
                ? money(botoxPlanning.patient_share_cents)
                : cignaPlanning
                  ? money(cignaPlanning.patient_share_cents)
                  : bluecardDeductible
                    ? money(
                        bluecardDeposit
                          ? result.collection_guidance!.amount_cents!
                          : bluecardDeductible.collect,
                      )
                    : aetnaPlanning
                      ? money(aetnaBalance!)
                      : diagnosticCopayReference != null
                        ? money(diagnosticCopayReference)
                        : officeBalance != null
                          ? officePolicyPending
                            ? `Pending · ${money(officeBalance)}`
                            : money(officeBalance)
                          : pendingAetnaCopay != null
                            ? `Pending · ${money(pendingAetnaCopay)}`
                            : pendingBluecardCopay != null
                              ? `Pending · ${money(pendingBluecardCopay)}`
                              : pendingCignaCopay != null
                                ? `Pending · ${money(pendingCignaCopay)}`
                                : partialCopay != null
                                  ? money(partialCopay)
                                  : cardPlanningCopay != null
                                    ? money(cardPlanningCopay)
                                    : priorDateAetnaCopay != null
                                      ? money(priorDateAetnaBalance!)
                                      : needsAction
                                        ? action.title
                                        : model.amountText}
      </strong>
      {dsnpOffice ? (
        <p>
          {money(dsnpOffice.patient_share_cents)} provisional pre-Medicaid share for CPT{' '}
          {dsnpOffice.scenario_cpt}. BCBS Texas fee reference × Stedi office coinsurance; the UHC
          allowance and this member’s dual cost-sharing protection are unresolved. Do not collect
          this share at check-in.
        </p>
      ) : plan ? (
        <p>
          {money(plan.office_copay_cents)} office copay + {plan.coinsurance_percent}% ×{' '}
          {money(plan.diagnostic_allowed_cents)} diagnostic allowance ={' '}
          {money(plan.patient_share_cents)} before any recorded payment. Deductible and
          out-of-pocket balances were not returned.
        </p>
      ) : biopsyBalance != null ? (
        <p>
          {money(biopsyTarget!.reference_cents)} BCBS Texas reference for{' '}
          {biopsyTarget!.included_codes.includes('99213') ? 'CPT 99213 + CPT 11104' : 'CPT 11104'},
          capped by the reported out-of-pocket balance. This provisional target assumes the full
          deductible applies because UHC did not return a deductible rule. Confirm payer order,
          Medicare billing protection, and any prior payment before charging.{' '}
          {biopsyOfficePending ? 'The follow-up share is not included. ' : ''}Pathology is excluded.
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
      ) : bluecardDeductible ? (
        <p>
          {bluecardDeposit
            ? `Collect ${money(result.collection_guidance!.amount_cents!)} as a refundable deposit for CPT 99203. The table shows the fee-based calculation; apply the deposit to the processed claim and refund any excess. The payer allowance and final patient share remain unknown.`
            : `CPT 99203 uses a ${money(bluecardDeductible.reference)} clinic fee reference. The table below shows the deductible and coinsurance formula. This estimate assumes the fee and ${bluecardDeductible.familyDeductible ? 'family' : 'plan-wide'} deductible apply to this office visit. Confirm the payer allowance, final CPT, prior payments and clinic proxy collection rule before charging.`}
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
          {separateOfficeCopayRemaining != null
            ? `min(${money(officeTerms?.copay_cents || 0)} specialist office copay, ${money(separateOfficeCopayRemaining)} separate copay maximum remaining) = ${money(officePlan!)}. The general out-of-pocket balance does not reduce this separate copay.`
            : `${money(officePlan!)} specialist office copay + ${money(0)} deductible + 0% coinsurance.`}
          {result.encounter_summary?.payments_verified_cents
            ? ` Less ${money(result.encounter_summary.payments_verified_cents)} already paid = ${money(officeBalance)}.`
            : ' Confirm prior payments.'}{' '}
          {officePolicyPending
            ? 'Clinic policy approval is required before collection.'
            : 'Planning estimate; confirm the final allowed charge before collection.'}
        </p>
      ) : pendingAetnaCopay != null ? (
        <p>
          Stedi reported this specialist office copay. Confirm remaining deductible and
          out-of-pocket balances, conditional benefit terms, and prior payments before charging.
        </p>
      ) : pendingBluecardCopay != null ? (
        <p>
          Payer-reported specialist office copay after recorded payments. Confirm BlueCard provider
          participation, home-plan authorization, and collection policy before charging.
        </p>
      ) : pendingCignaCopay != null ? (
        <p>
          The reported office copay is conditional. Confirm it with the payer for this visit and
          CPT, the clinic collection policy, and any payments or credits before charging. Cigna’s
          allowed amount and full patient share remain unknown.
        </p>
      ) : diagnosticCopayReference != null ? (
        <p>
          One payer-reported diagnostic copay per day. Approve the clinic collection policy and
          check prior payments before charging.
        </p>
      ) : partialCopay != null ? (
        <p>
          {pendingUhcHmoPosCopay
            ? 'Stedi reported this specialist office copay. Confirm QMB protection, prior payments, and the current clinic CPT rate before charging; the UnitedHealthcare allowance and final claim share remain unknown.'
            : referenceOnly
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
      ) : priorDateAetnaCopay != null ? (
        <p>
          {aetnaMedicareScenario
            ? `${money(priorDateAetnaBalance!)} if Aetna is the only applicable payer; ${money(0)} at check-in if Medicare is confirmed secondary. Aetna's response is for another date. Verify visit-date coverage, provider network, Medicare entitlement and payer order before charging.`
            : 'The payer returned benefits for a different date. Confirm coverage for this visit, network, billing protections, and prior payments before charging. The amount to collect remains unestablished.'}
        </p>
      ) : needsAction ? (
        <p>{action.hint}</p>
      ) : model.guidance?.basis === 'published_geha_pshb_zero_office' && model.amount ? (
        <p>
          One covered in-network office visit has a $0 published plan share. The UHC allowed amount
          remains unknown; final coding and claim adjudication control.
        </p>
      ) : model.previousEstimate ? (
        <p>Recheck this saved visit before collecting.</p>
      ) : model.provisionalAmount != null || model.estimatedShare ? (
        <p>Planning estimate only. See the collection decision below.</p>
      ) : model.guidance?.basis === 'claim_after_service' && model.amount ? (
        <p>Claim first. Patient responsibility follows the processed claim.</p>
      ) : model.guidance?.basis === 'cigna_refundable_office_copay' && model.amount ? (
        <p>
          Refundable Cigna office copay advance; provider network and final share remain
          unconfirmed.
        </p>
      ) : model.guidance?.collection_rule?.mode === 'bluecard_epo_neurology_office_copay' &&
        model.amount ? (
        <p>
          One payer-reported in-network neurologist office copay after recorded payments. Separately
          performed testing and the final claim may change the total.
        </p>
      ) : model.guidance?.basis === 'office_copay' && model.amount ? (
        <p>Office copay advance; other service costs may follow the claim.</p>
      ) : model.guidance?.basis === 'diagnostic_day_copay' && model.amount ? (
        <p>One diagnostic copay advance; deductible and final patient share follow the claim.</p>
      ) : null}
    </>
  )
}
