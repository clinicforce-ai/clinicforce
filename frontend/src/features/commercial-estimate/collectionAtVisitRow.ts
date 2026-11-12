import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { feeProxyFormula } from './coinsuranceFormula'
import { money } from './format'
import { patientBenefitsBound } from './identityBinding'
import type { bluecardOfficeDeductibleScenario } from './bluecardOfficeDeductibleScenario'

export function pendingBluecardOfficeCopay(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
): number | null {
  const line =
    result.price_range?.mode === 'services' && result.price_range.lines?.length === 1
      ? result.price_range.lines[0]
      : null
  const terms = line?.benefits
  const copay = terms?.copay_cents
  if (
    model.cash ||
    model.calculating ||
    model.previousEstimate ||
    model.amount ||
    result.collection_guidance?.amount_cents != null ||
    !/\b(?:blue cross|bcbs(?:tx| texas)?)\b/i.test(result.payer || '') ||
    result.plan_details?.stedi?.names?.length !== 1 ||
    !/\b(?:PPO|Preferred Provider Organization)\b/i.test(result.plan_details.stedi.names[0]) ||
    result.benefits?.active !== true ||
    result.benefits.service_date_matches === false ||
    result.benefits.has_secondary ||
    result.benefits.network_conflict ||
    result.benefits.coverage_status_conflict ||
    result.benefits.coverage_dates_invalid ||
    result.benefits.historical_only ||
    result.benefits.source_errors?.length ||
    result.benefits.network_status === 'out_of_network' ||
    result.billing_protection?.status === 'qmb' ||
    result.recovery?.reason_code === 'multiple_active_policies' ||
    !patientBenefitsBound(result.identity) ||
    !line ||
    !/^992(?:0[2-5]|1[2-5])$/.test(line.code) ||
    line.units !== 1 ||
    line.modifiers?.length ||
    terms?.active !== true ||
    terms.network_status === 'out_of_network' ||
    terms.service_date_matches === false ||
    terms.has_secondary ||
    terms.coverage_status_conflict ||
    terms.coverage_dates_invalid ||
    terms.source_errors?.length ||
    (terms.copay_period && !['VISIT', '27', 'DAY', '7'].includes(terms.copay_period)) ||
    (terms.deductible_applies !== false && terms.deductible_remaining_cents !== 0) ||
    (terms.coinsurance_percent != null && Number(terms.coinsurance_percent) !== 0) ||
    !Number.isInteger(copay) ||
    copay! <= 0 ||
    (terms.separate_copay_limit
      ? terms.copay_remaining_cents == null || terms.copay_remaining_cents < copay!
      : terms.oop_remaining_cents == null || terms.oop_remaining_cents < copay!)
  )
    return null
  return Math.max(0, copay! - (result.encounter_summary?.payments_verified_cents || 0))
}

export function pendingCignaOfficeCopay(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
): number | null {
  const officeLine =
    result.price_range?.mode === 'services' && result.price_range.lines?.length === 1
      ? result.price_range.lines[0]
      : null
  return !model.cash &&
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
}

export function collectionAtVisitRow(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
  values: {
    combinedPlan: PrevisitEstimate['combined_emg_plan']
    feeProxy: PrevisitEstimate['fee_proxy_estimate']
    aetnaPlanning: PrevisitEstimate['aetna_diagnostic_reference'] | null
    aetnaCopayPlanning: number | null
    officeReferenceCopay: number | null
    humanaReferenceCopay: number | null
    partialOfficeCopay: number | null
    pendingAetnaCopay: number | null
    priorDateAetnaCopay: number | null
    pendingBluecardCopay: number | null
    cardPlanningCopay: number | null
    cignaCard:
      NonNullable<PrevisitEstimate['card_check']>['cigna_card_reference'] | null | undefined
    automaticCashFallback: boolean
    cignaFeeProxy: boolean
    proxyFormula: ReturnType<typeof feeProxyFormula> | undefined
    bluecardDeductible: ReturnType<typeof bluecardOfficeDeductibleScenario>
  },
): CostBreakdownRow {
  const {
    combinedPlan,
    feeProxy,
    aetnaPlanning,
    aetnaCopayPlanning,
    officeReferenceCopay,
    humanaReferenceCopay,
    partialOfficeCopay,
    pendingAetnaCopay,
    priorDateAetnaCopay,
    pendingBluecardCopay,
    cardPlanningCopay,
    cignaCard,
    automaticCashFallback,
    cignaFeeProxy,
    proxyFormula,
    bluecardDeductible,
  } = values
  const choicePlusEmg = feeProxy?.basis === 'bcbstx_uhc_choice_plus_fee_proxy'
  const dsnpOffice = feeProxy?.basis === 'bcbstx_uhc_dsnp_office_proxy'
  const feeProxyCheckin =
    feeProxy &&
    !dsnpOffice &&
    !cignaFeeProxy &&
    feeProxy.basis !== 'cigna_clinic_fee_quote' &&
    (!choicePlusEmg ||
      (!model.previousEstimate &&
        !model.calculating &&
        !model.requirementIssue &&
        !['claim_after_service', 'qmb_protection'].includes(
          result.collection_guidance?.basis || '',
        )))
      ? Math.max(
          0,
          feeProxy.patient_share_cents - (result.encounter_summary?.payments_verified_cents || 0),
        )
      : null
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
  const aetnaPaid = result.encounter_summary?.payments_verified_cents || 0
  const aetnaBalance = aetnaPlanning
    ? Math.max(0, aetnaPlanning.patient_share_cents - aetnaPaid)
    : null
  const officeBalance =
    aetnaCopayPlanning == null ? null : Math.max(0, aetnaCopayPlanning - aetnaPaid)
  const aetnaOfficeTerms =
    result.price_range?.mode === 'services' && result.price_range.lines?.length === 1
      ? result.price_range.lines[0].benefits
      : null
  const aetnaSeparateCopayCap = aetnaOfficeTerms?.separate_copay_limit
    ? aetnaOfficeTerms.copay_remaining_cents
    : null
  const aetnaPolicyPending =
    aetnaCopayPlanning != null &&
    result.collection_guidance?.positive_advance_policy_missing === true
  const aetnaOtherCoverage =
    aetnaCopayPlanning != null &&
    (result.benefits?.has_secondary === true ||
      result.recovery?.reason_code === 'multiple_active_policies')
  const medicareSecondary = /^medicare\b/i.test(
    result.benefits?.primary_secondary_order?.secondary_payer || '',
  )
  const medicareReported = /^medicare\b/i.test(
    (result.reference_benefits ?? result.benefits)?.reported_secondary_payer?.payer || '',
  )
  const aetnaMedicareScenario = medicareSecondary || medicareReported
  const payerOrderVerified = result.benefits?.primary_secondary_order?.status === 'verified'
  const priorDateAetnaBalance =
    priorDateAetnaCopay == null ? null : Math.max(0, priorDateAetnaCopay - aetnaPaid)
  const botoxPreview = !model.cash && !model.previousEstimate ? result.botox_preview : null
  const pendingCignaCopay = pendingCignaOfficeCopay(result, model)
  const pendingUhcHmoPosCopay =
    partialOfficeCopay != null &&
    /\bhmopos\b/i.test(result.plan_details?.stedi?.names?.[0] || '') &&
    result.billing_protection?.status !== 'not_qmb'
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
  const collectionBlocker = result.payment_explanation
  const namedCollectionAction =
    collectionBlocker?.reason_code === 'clinic_product_mismatch'
      ? 'Match member plan to clinic agreement'
      : collectionBlocker?.reason_code === 'contract_rate_missing'
        ? 'Check plan-to-rate match for selected CPT'
        : null
  const pendingCollectionAction =
    namedCollectionAction ||
    collectionBlocker?.sentence ||
    'Review selected CPT, benefits and clinic rate before collecting'
  const row = {
    label:
      result.collection_guidance?.basis === 'cigna_clinic_fee_quote' &&
      result.collection_guidance.amount_cents != null
        ? 'Amount to collect at visit · refundable clinic quote'
        : dsnpOffice
          ? 'Amount to collect at visit · dual status unresolved'
          : bluecardDeductible?.familyDeductible
            ? 'Amount to collect at visit · provisional target'
            : biopsyBalance != null
              ? biopsyOfficePending
                ? 'Provisional biopsy target · office share pending'
                : 'Provisional collection target · confirmation pending'
              : aetnaOtherCoverage
                ? payerOrderVerified
                  ? 'Amount to collect at visit · pending secondary claim'
                  : 'Amount to collect at visit · pending payer order'
                : pendingAetnaCopay != null || pendingBluecardCopay != null
                  ? 'Amount to collect at visit · pending verification'
                  : priorDateAetnaBalance != null
                    ? aetnaMedicareScenario
                      ? 'Amount to collect at visit · Aetna / Medicare scenario'
                      : 'Amount to collect at visit · office copay reference'
                    : cignaCard
                      ? 'Amount to collect at visit · pending verification'
                      : botoxPreview
                        ? 'Planned amount to collect at visit'
                        : diagnosticCopayReference != null
                          ? 'Diagnostic copay · collection pending'
                          : pendingUhcHmoPosCopay
                            ? 'Amount to collect at visit · verify Medicare protection'
                            : (partialOfficeCopay != null || cardPlanningCopay != null) &&
                                feeProxyCheckin == null
                              ? 'Suggested amount to collect at visit'
                              : ((cignaFeeProxy &&
                                    !['claim_after_service', 'office_copay'].includes(
                                      result.collection_guidance?.basis || '',
                                    )) ||
                                    feeProxyCheckin != null ||
                                    aetnaPlanning != null ||
                                    aetnaCopayPlanning != null) &&
                                  !['bcbstx_proxy_advance', 'qmb_protection'].includes(
                                    result.collection_guidance?.basis || '',
                                  )
                                ? aetnaPolicyPending
                                  ? 'Amount to collect at visit · pending policy approval'
                                  : 'Planned amount to collect at visit'
                                : 'Amount to collect at visit',
    value:
      result.collection_guidance?.basis === 'cigna_clinic_fee_quote' &&
      result.collection_guidance.amount_cents != null
        ? money(result.collection_guidance.amount_cents)
        : dsnpOffice
          ? `${money(0)} at check-in · ${money(feeProxy!.patient_share_cents)} pre-Medicaid planning share`
          : combinedPlan
            ? `${money(combinedPlan.amount_cents)} planning estimate`
            : result.collection_guidance?.basis === 'healthselect_ma_zero_televisit' &&
                result.collection_guidance.amount_cents === 0
              ? money(0)
              : result.collection_guidance?.basis === 'published_geha_pshb_zero_office' &&
                  result.collection_guidance.amount_cents === 0
                ? money(0)
                : result.collection_guidance?.basis === 'qmb_protection' &&
                    result.collection_guidance.amount_cents === 0
                  ? money(0)
                  : biopsyBalance != null
                    ? `${money(biopsyBalance)} provisional${biopsyOfficePending ? ' biopsy only' : ''}`
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
                          : bluecardDeductible
                            ? `${money(bluecardDeductible.collect)} estimated · confirm before charging`
                            : diagnosticCopayReference != null
                              ? `${money(diagnosticCopayReference)} once per day · verify before charging`
                              : cignaFeeProxy
                                ? `${money(feeProxy!.patient_share_cents)} planning estimate`
                                : feeProxyCheckin != null
                                  ? money(feeProxyCheckin)
                                  : aetnaPlanning
                                    ? `${money(aetnaBalance!)} planning estimate`
                                    : aetnaCopayPlanning != null
                                      ? aetnaOtherCoverage
                                        ? `Pending · ${money(officeBalance!)} Aetna office copay before secondary processing`
                                        : aetnaPolicyPending
                                          ? `Pending · ${money(officeBalance!)} specialist office copay`
                                          : `${money(officeBalance!)} planning estimate`
                                      : pendingBluecardCopay != null
                                        ? `Pending · ${money(pendingBluecardCopay)} office copay if verified`
                                        : officeReferenceCopay != null
                                          ? `${money(officeReferenceCopay)} office-only reference`
                                          : humanaReferenceCopay != null
                                            ? `Pending · ${money(humanaReferenceCopay)} office copay if confirmed`
                                            : partialOfficeCopay != null
                                              ? pendingUhcHmoPosCopay
                                                ? `${money(partialOfficeCopay)} specialist office copay`
                                                : money(partialOfficeCopay)
                                              : pendingAetnaCopay != null
                                                ? `Pending · ${money(pendingAetnaCopay)} office copay if verified`
                                                : priorDateAetnaBalance != null
                                                  ? aetnaMedicareScenario
                                                    ? `${money(priorDateAetnaBalance)} if Aetna is the only applicable payer · ${money(0)} if Medicare is confirmed secondary`
                                                    : money(priorDateAetnaBalance)
                                                  : cardPlanningCopay != null
                                                    ? `${money(cardPlanningCopay)} office-only card suggestion`
                                                    : pendingCignaCopay != null
                                                      ? `Pending · ${money(pendingCignaCopay)} office copay if verified`
                                                      : cignaCard
                                                        ? `Pending · ${money(cignaCard.nontier1_specialist_copay_cents)} non-Tier-1 office-only reference; diagnostic CPT amount requires verification`
                                                        : pendingCollectionAction,
    detail:
      result.collection_guidance?.collection_rule?.mode === 'bluecard_epo_neurology_office_copay' &&
      model.supportedAmount
        ? `One ${money(result.price_range?.lines?.[0]?.benefits?.copay_cents || 0)} in-network neurologist office copay per date of service and provider${result.encounter_summary?.payments_verified_cents ? ` less ${money(result.encounter_summary.payments_verified_cents)} already paid` : ''} = ${money(result.collection_guidance.amount_cents!)} to collect. The payer allowed amount and any separately performed testing follow the claim.`
        : dsnpOffice
          ? `${proxyFormula?.total} ${feeProxy!.message}`
          : combinedPlan
            ? `${money(combinedPlan.office_copay_cents)} + ${combinedPlan.coinsurance_percent}% × ${money(combinedPlan.diagnostic_allowed_cents)} = ${money(combinedPlan.patient_share_cents)}${combinedPlan.payments_cents ? `; less ${money(combinedPlan.payments_cents)} already paid` : ''}. Separate follow-up E/M is planned; deductible and out-of-pocket adjustments remain assumptions until payer balances are returned.`
            : result.collection_guidance?.basis === 'healthselect_ma_zero_televisit'
              ? '$0.00 at check-in from the payer’s $0 virtual-visit copay, ERS no-deductible rule and 0% coinsurance. Final billed code, payer order and claim liability remain open.'
              : result.collection_guidance?.basis === 'published_geha_pshb_zero_office'
                ? result.collection_guidance.reason
                : result.collection_guidance?.basis === 'qmb_protection'
                  ? 'QMB protection prohibits collecting Medicare cost sharing for covered care'
                  : biopsyBalance != null
                    ? `min(${money(biopsyTarget!.reference_cents)} BCBS Texas CPT reference, ${money(biopsyTarget!.oop_remaining_cents)} remaining out-of-pocket) = ${money(biopsyTarget!.amount_cents)}${result.encounter_summary?.payments_verified_cents ? `; less ${money(result.encounter_summary.payments_verified_cents)} already paid = ${money(biopsyBalance)}` : ''}. ${biopsyTarget!.message}`
                    : bluecardDeductible
                      ? `Deductible: min(${money(bluecardDeductible.reference)}, ${money(bluecardDeductible.deductibleRemaining)}) = ${money(bluecardDeductible.deductible)}; coinsurance: 20% × (${money(bluecardDeductible.reference)} − ${money(bluecardDeductible.deductible)}) = ${money(bluecardDeductible.coinsurance)}; total: min(${money(bluecardDeductible.deductible + bluecardDeductible.coinsurance)}, ${money(bluecardDeductible.oopRemaining)} out-of-pocket remaining) = ${money(bluecardDeductible.patientShare)}${bluecardDeductible.paid ? `; less ${money(bluecardDeductible.paid)} already paid = ${money(bluecardDeductible.collect)}` : ''}. Assumes the BCBSTX clinic fee and ${bluecardDeductible.familyDeductible ? 'family' : 'general'} deductible apply to this visit; confirm the allowance, CPT and clinic collection rule before charging.`
                      : botoxPreview
                        ? `${botoxPreview.coinsurance_percent}% × ${money(botoxPreview.allowed_cents)} for CPT ${botoxPreview.code} = ${money(botoxPreview.patient_share_cents)}. Confirm the injection site, individual limits, and prior payments before charging.`
                        : cignaFeeProxy &&
                            result.collection_guidance?.basis === 'claim_after_service'
                          ? result.collection_guidance.reason
                          : diagnosticCopayReference != null
                            ? 'Payer-reported diagnostic copay for the selected testing. Approve the clinic collection policy and account for prior payments before charging; the full patient share remains unresolved.'
                            : cignaFeeProxy && result.collection_guidance?.basis !== 'office_copay'
                              ? `${proxyFormula?.total} ${feeProxy!.message || result.collection_guidance?.reason || ''}`
                              : feeProxy != null &&
                                  result.collection_guidance?.basis !== 'office_copay'
                                ? result.collection_guidance?.basis === 'bcbstx_proxy_advance'
                                  ? `${proxyFormula?.total} Clinic-approved BCBS Texas fee proxy advance; final responsibility follows the UHC claim.`
                                  : `${proxyFormula?.total} ${
                                      choicePlusEmg
                                        ? 'Provisional BCBSTX clinic fee scenario; deductible applicability and UHC allowance require confirmation before charging.'
                                        : 'Provisional BCBS Texas fee proxy; collect only after clinic policy and Medicare billing-protection clearance.'
                                    }`
                                : aetnaPlanning
                                  ? `min((${money(aetnaPlanning.copay_cents / 2)} per test × 2) + ${money(aetnaPlanning.deductible_cents)} deductible + ${money(aetnaPlanning.coinsurance_cents)} coinsurance, ${money(aetnaPlanning.estimated_allowed_cents)} Aetna fee reference, ${money(aetnaPlanning.oop_remaining_cents)} remaining out-of-pocket) = ${money(aetnaPlanning.patient_share_cents)}${aetnaPaid ? `; less ${money(aetnaPaid)} already paid = ${money(aetnaBalance!)}` : ' before any prior payment'}. ${aetnaPlanning.message}`
                                  : aetnaCopayPlanning != null
                                    ? aetnaOtherCoverage
                                      ? `${money(aetnaCopayPlanning)} Aetna specialist office copay + ${money(0)} office deductible + 0% office coinsurance = ${money(aetnaCopayPlanning)} before secondary processing. ${medicareSecondary ? 'Medicare is reported as secondary; send the primary claim, then Medicare, and collect only any balance after its remittance.' : 'Confirm payer order and the secondary benefit before collecting.'}`
                                      : `${
                                          aetnaSeparateCopayCap != null
                                            ? `min(${money(aetnaOfficeTerms?.copay_cents || 0)} specialist office copay, ${money(aetnaSeparateCopayCap)} separate copay maximum remaining) = ${money(aetnaCopayPlanning)}. The general out-of-pocket balance does not reduce this separate copay.`
                                            : `${money(aetnaCopayPlanning)} specialist office copay + ${money(0)} deductible + 0% coinsurance = ${money(aetnaCopayPlanning)}.`
                                        }${aetnaPaid ? ` Less ${money(aetnaPaid)} already paid = ${money(officeBalance!)}.` : ' Confirm prior payments.'} ${aetnaPolicyPending ? 'Collection is pending clinic policy approval.' : 'Planning amount.'} Confirm the final allowed charge before collection.`
                                    : pendingBluecardCopay != null
                                      ? 'Payer-reported BlueCard specialist copay for one CPT 99204 office visit, less recorded payments. Confirm member plan, provider participation, home-plan authorization, and collection policy before charging.'
                                      : officeReferenceCopay != null
                                        ? 'Planning figure for one specialist office visit on the appointment date; not an authorized collection instruction. Other services, prior payments and Medicare cost-sharing protection are not included.'
                                        : humanaReferenceCopay != null
                                          ? 'Published in-network specialist copay only. Confirm the member card, provider network, Medicare billing protection, clinic collection policy, and prior payments before charging.'
                                          : partialOfficeCopay != null
                                            ? pendingUhcHmoPosCopay
                                              ? 'Payer-reported specialist office copay after recorded payments. Confirm QMB protection, prior payments, and the current clinic CPT rate before charging; the UnitedHealthcare allowance and final claim share remain unknown.'
                                              : 'Suggested office visit copay after recorded payments. Separately performed DBS device services may add cost.'
                                            : pendingAetnaCopay != null
                                              ? 'Stedi reported one specialist office copay for CPT 99204. Confirm the remaining deductible, out-of-pocket balance, conditional benefit, prior payments, and clinic collection policy before charging. Full patient share remains unresolved.'
                                              : priorDateAetnaBalance != null
                                                ? `Payer-reported in-network specialist office copay after recorded payments. The saved response covers another date; confirm visit-date coverage and the provider's network. ${aetnaMedicareScenario ? 'Aetna reports Medicare as secondary; verify entitlement and payer order. If confirmed, collect $0 at check-in, submit the primary and Medicare secondary claims, and collect only any balance after adjudication.' : 'Confirm other coverage and clinic collection policy before charging.'} This reference does not authorize a charge.`
                                                : cardPlanningCopay != null
                                                  ? 'Printed specialist copay for one office visit; verify before charging. Planned imaging and final payer share are unresolved.'
                                                  : pendingCignaCopay != null
                                                    ? 'Complete the visit-date payer check, confirm this copay and the clinic collection policy, and account for verified payments before charging. Full patient share remains unresolved.'
                                                    : cignaCard
                                                      ? 'Card reference only: confirm the visit-date coverage, provider tier, separately documented office E/M, clinic collection policy, and prior payments before charging. Diagnostic CPT share remains unknown.'
                                                      : automaticCashFallback
                                                        ? 'Insurance collection is unresolved; any cash quote is listed separately'
                                                        : result.collection_guidance
                                                              ?.unconfirmed_zero_oop
                                                          ? 'Confirm the reported $0 out-of-pocket balance with the payer before collection'
                                                          : model.supportedAmount
                                                            ? result.collection_guidance
                                                                ?.primary_secondary_copay
                                                              ? 'Provisional primary office copay; the secondary claim may change final responsibility'
                                                              : result.collection_guidance
                                                                    ?.basis === 'office_copay'
                                                                ? 'Office copay advance; the payer allowed amount and final share remain unresolved'
                                                                : result.collection_guidance
                                                                      ?.basis ===
                                                                    'out_of_pocket_limit'
                                                                  ? 'Applicable out-of-pocket maximum has no remaining balance for covered care'
                                                                  : result.collection_guidance
                                                                        ?.basis ===
                                                                      'diagnostic_day_copay'
                                                                    ? 'One diagnostic copay advance; other costs may follow the claim'
                                                                    : result.collection_guidance
                                                                          ?.basis ===
                                                                        'claim_after_service'
                                                                      ? 'Clinic policy defers collection until after the claim'
                                                                      : undefined
                                                            : namedCollectionAction
                                                              ? collectionBlocker?.sentence
                                                              : undefined,
    emphasis: true,
  }
  if (
    model.supportedAmount &&
    result.price_range?.mode === 'alternatives' &&
    result.collection_guidance?.basis === 'out_of_pocket_limit' &&
    result.collection_guidance.amount_cents === 0
  ) {
    row.detail = `For each priced CPT: min(reported copay + any applicable deductible and coinsurance, ${money(0)} individual in-network out-of-pocket remaining) = ${money(0)} patient share. The family out-of-pocket remainder is a limit, not a visit charge. Choose the performed CPT before billing.`
  }
  if (result.collection_guidance?.basis === 'bcbstx_refundable_advance') {
    row.label = ['blue_ppo_family_deductible_quote', 'cigna_ppo_deductible_quote'].includes(
      result.collection_guidance.collection_rule?.mode || '',
    )
      ? 'Amount to collect at visit'
      : ['trs_hd_office_refundable_deposit', 'bluecard_family_office_refundable_deposit'].includes(
            result.collection_guidance.collection_rule?.mode || '',
          )
        ? 'Refundable deposit at visit'
        : 'Refundable advance at visit'
    row.detail =
      result.collection_guidance.collection_rule?.mode ===
        'bluecard_family_office_refundable_deposit' && bluecardDeductible
        ? `Deductible: min(${money(bluecardDeductible!.reference)}, ${money(bluecardDeductible!.deductibleRemaining)}) = ${money(bluecardDeductible!.deductible)}; coinsurance: 20% × (${money(bluecardDeductible!.reference)} − ${money(bluecardDeductible!.deductible)}) = ${money(bluecardDeductible!.coinsurance)}; capped at ${money(bluecardDeductible!.oopRemaining)} out-of-pocket remaining${bluecardDeductible!.paid ? `; less ${money(bluecardDeductible!.paid)} already paid` : ''}. ${result.collection_guidance.reason}`
        : ['blue_ppo_family_deductible_quote', 'cigna_ppo_deductible_quote'].includes(
              result.collection_guidance.collection_rule?.mode || '',
            )
          ? result.collection_guidance.reason
          : [
                'trs_hd_office_refundable_deposit',
                'bluecard_family_office_refundable_deposit',
              ].includes(result.collection_guidance.collection_rule?.mode || '')
            ? result.collection_guidance.reason
            : 'The payer returned no financial terms. This refundable advance uses the selected CPTs’ BCBS Texas fee references; it is separate from estimated patient liability. Apply it to the processed claim and refund any excess.'
  }
  if (result.collection_guidance?.basis === 'cigna_refundable_office_copay') {
    row.label = 'Refundable Cigna copay advance at visit'
    row.detail =
      'Current Stedi benefits report this POS 11 specialist office copay. Provider participation for the exact product is still unconfirmed. Apply the advance to the processed claim and refund any excess.'
  }
  if (result.collection_guidance?.basis === 'aetna_office_refundable_deposit') {
    row.label = 'Refundable deposit at visit'
    row.detail = result.collection_guidance.reason
  }
  if (
    !model.cash &&
    !model.previousEstimate &&
    !model.calculating &&
    ['cms_msp_part_b_deferral', 'aetna_choice_family_oop_deferral'].includes(
      result.collection_guidance?.collection_rule?.mode || '',
    )
  ) {
    row.label = 'Amount to collect at visit'
    row.value = money(0)
    row.detail = result.collection_guidance?.reason
  }
  return row
}
