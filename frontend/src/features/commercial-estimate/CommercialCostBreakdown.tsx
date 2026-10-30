import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { serviceRowLabel } from '../insurance-estimate/serviceRowLabel'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { coinsuranceFormula, feeProxyFormula } from './coinsuranceFormula'
import CommercialCostTables from './CommercialCostTables'
import { collectionAtVisitRow } from './collectionAtVisitRow'
import { money, range } from './format'
import { requirementStatus } from './commercialRequirementStatus'
import {
  aetnaOfficeReferenceCopay,
  aetnaOfficePlanningCopay,
  cardOfficePlanningCopay,
  payerReportedPartialCopay,
  publishedHumanaOfficeCopay,
  specialistOfficeCoinsurance,
  specialistOfficeCopay,
} from './payerReportedPartialCopay'
import { referenceCoverageRow } from './referenceCoverageRow'
import { limitSummary, unknown } from './limitSummary'
import { resolvedAllowedTotal } from './resolvedAllowedTotal'
import { commercialCptRows } from './commercialCptRows'
import { commercialOopRows } from './commercialOopRows'
export default function CommercialCostBreakdown({
  model,
  result,
}: {
  model: CommercialEstimateModel
  result: PrevisitEstimate
}) {
  const benefits = result.reference_benefits ?? result.benefits
  const reportedResult = result.reference_benefits ? { ...result, benefits } : result
  const price = result.price_range
  const feeProxy = result.fee_proxy_estimate
  const botoxPreview = !model.cash && !model.previousEstimate ? result.botox_preview : null
  const combinedPlan = !model.cash && !model.previousEstimate ? result.combined_emg_plan : null
  const cignaFeeProxy = feeProxy?.basis === 'bcbstx_cigna_fee_proxy'
  const cignaOfficeScenario = cignaFeeProxy && !!feeProxy.scenario_kind
  const lines = price?.mode === 'services' ? price.lines || [] : []
  const cptRows = commercialCptRows(result, lines, botoxPreview)
  const reason = result.reason || model.anticipatedService || 'Not recorded'
  const visitReason =
    result.service_plan?.kind === 'emg_ncs' &&
    !/\b(?:emg|ncs|electromyograph|nerve conduction)\b/i.test(reason)
      ? `${reason} · EMG/NCS evaluation`
      : reason
  const familyOopApplied =
    result.collection_guidance?.basis === 'out_of_pocket_limit' &&
    lines.length > 0 &&
    lines.every((line) => line.benefits?.family_oop_zero_applies)
  const automaticCashFallback = model.cash && result.cash_fallback != null
  const summary = result.encounter_summary
  const proxyFormula = feeProxy && feeProxyFormula(feeProxy, summary?.payments_verified_cents || 0)
  const individualDeductible = limitSummary(reportedResult, 'deductible', 'INDIVIDUAL')
  const surgicalIndividualDeductible = limitSummary(reportedResult, 'deductible', 'INDIVIDUAL', '2')
  const familyDeductible = limitSummary(reportedResult, 'deductible', 'FAMILY')
  const surgicalFamilyDeductible = limitSummary(reportedResult, 'deductible', 'FAMILY', '2')
  const lineCoinsurance = [...new Set(lines.map((line) => line.benefits?.coinsurance_percent))]
  const reportedCoinsurance = result.reference_benefits
    ? specialistOfficeCoinsurance(reportedResult)
    : lines.length
      ? lineCoinsurance.length === 1
        ? lineCoinsurance[0]
        : null
      : benefits?.coinsurance_percent
  const reportedOfficeCopay = specialistOfficeCopay(reportedResult)
  const officeReferenceCopay = aetnaOfficeReferenceCopay(result, model)
  const humanaReferenceCopay = publishedHumanaOfficeCopay(result, model)
  const partialOfficeCopay = payerReportedPartialCopay(result, model)
  const cardPlanningCopay =
    partialOfficeCopay == null && officeReferenceCopay == null && humanaReferenceCopay == null
      ? cardOfficePlanningCopay(result, model)
      : null
  const components = summary?.components
  const diagnosticCopay =
    (result.collection_guidance?.basis === 'diagnostic_day_copay' ||
      result.collection_guidance?.diagnostic_copay_reference_cents != null) &&
    lines.length > 0 &&
    lines.every((line) => line.benefits?.copay_cents === lines[0].benefits?.copay_cents)
      ? lines[0].benefits?.copay_cents
      : null
  const pricedCashLines = lines.filter((line) => line.cash_cents != null && !line.cash_included)
  const lineAllowedTotal = resolvedAllowedTotal(lines)
  const calculated =
    !automaticCashFallback &&
    !model.previousEstimate &&
    !summary?.integrity_error &&
    price?.complete === true &&
    (summary?.complete === true || model.supportedAmount === true)
  const temporaryRateReference =
    /\baetna\b/i.test(result.payer || '') &&
    !result.temporary_rate_reference?.source?.startsWith('Aetna MEPPO')
      ? null
      : result.temporary_rate_reference
  const aetnaRateReference = Boolean(temporaryRateReference?.source?.startsWith('Aetna MEPPO'))
  const aetnaPlanning =
    !model.cash && !model.previousEstimate && result.collection_guidance?.amount_cents == null
      ? result.aetna_diagnostic_reference
      : null
  const aetnaCopayPlanning = aetnaOfficePlanningCopay(result, model, lines)
  const insuredRows: CostBreakdownRow[] = [
    referenceCoverageRow(result, model, benefits),
    { label: 'Network', value: model.networkLabel },
    {
      label: result.reason ? 'Reason for visit' : 'Planned service',
      value: visitReason,
    },
    cptRows.cpt,
    ...cptRows.candidates,
    ...(lines.length
      ? lines.map((line) => {
          const proxyRate = temporaryRateReference?.lines.find(
            (rate) =>
              rate.id === line.id &&
              rate.code === line.code &&
              rate.units === line.units &&
              (rate.modifiers || []).join() === (line.modifiers || []).join(),
          )
          const resolved =
            !automaticCashFallback &&
            line.rate_resolution?.status === 'RESOLVED' &&
            ['clinic_contract', 'payer_out_of_network', 'cms_original_medicare_estimate'].includes(
              line.allowed_basis || '',
            )
          return {
            label: serviceRowLabel({
              description: line.description,
              reason: result.reason || model.anticipatedService,
              code: line.code,
              units: line.units,
            }),
            value:
              resolved && line.allowed_cents != null
                ? money(line.allowed_cents)
                : proxyRate
                  ? `${money(proxyRate.unit_reference_cents * (Number(line.units) || 1))} ${aetnaRateReference ? 'Aetna fee reference' : 'BCBS Texas reference'}`
                  : 'Allowed amount unavailable',
            detail:
              resolved && line.unit_allowed_cents != null
                ? `${money(line.unit_allowed_cents)} per unit × ${line.units || 1}`
                : proxyRate
                  ? `${money(proxyRate.unit_reference_cents)} per unit × ${line.units || 1}; ${result.payer || 'payer'} allowed amount unknown`
                  : line.description || undefined,
          }
        })
      : [cptRows.unpriced]),
    {
      label: 'Total payer allowed',
      value:
        cardPlanningCopay != null
          ? 'Payer allowance pending'
          : automaticCashFallback
            ? 'Not established'
            : price?.allowed_min_cents != null && price.allowed_max_cents != null
              ? range(price.allowed_min_cents, price.allowed_max_cents)
              : lineAllowedTotal != null
                ? money(lineAllowedTotal)
                : botoxPreview
                  ? `${money(botoxPreview.allowed_cents)} · clinic fee row`
                  : 'Not fully priced',
    },
    ...(temporaryRateReference?.lines.length
      ? [
          {
            label: feeProxy
              ? 'BCBS Texas diagnostic fee proxy total'
              : aetnaRateReference
                ? 'Aetna fee reference'
                : 'Blue Cross fee reference',
            value: feeProxy
              ? money(feeProxy.reference_total_cents)
              : aetnaRateReference
                ? `${money(temporaryRateReference.lines.reduce((sum, line) => sum + line.unit_reference_cents * (Number(line.units) || 1), 0))} total reference`
                : temporaryRateReference.lines
                    .map((line) => `CPT ${line.code}: ${money(line.unit_reference_cents)} per unit`)
                    .join('; '),
            detail: temporaryRateReference.message,
          },
        ]
      : []),
    {
      label: 'Copay',
      value: aetnaPlanning
        ? `${money(aetnaPlanning.copay_cents / lines.length)} per diagnostic test × ${lines.length} = ${money(aetnaPlanning.copay_cents)}`
        : familyOopApplied
          ? '$0 payable after family out-of-pocket limit'
          : combinedPlan
            ? `${money(combinedPlan.office_copay_cents)} specialist follow-up`
            : cignaOfficeScenario
              ? `${money(feeProxy!.copay_cents)} specialist office visit · assumed once`
              : feeProxy != null
                ? cignaFeeProxy && feeProxy.copay_unconfirmed
                  ? `${money(0)} assumed for calculation · no CPT copay returned`
                  : feeProxy.copay_cents
                    ? `${money(feeProxy.copay_cents)} specialist follow-up; $0.00 diagnostic copay`
                    : '$0.00 diagnostic copay'
                : botoxPreview
                  ? 'Not returned for office injection'
                  : officeReferenceCopay != null
                    ? `${money(officeReferenceCopay)} specialist office visit · payer-reported reference`
                    : humanaReferenceCopay != null
                      ? `${money(humanaReferenceCopay)} in-network specialist office visit · published plan reference`
                      : diagnosticCopay != null
                        ? `${money(diagnosticCopay)} once per day`
                        : calculated && components?.copay_cents != null
                          ? money(components.copay_cents)
                          : lines.some((line) => line.benefits?.copay_cents != null)
                            ? lines
                                .map(
                                  (line) =>
                                    `CPT ${line.code}: ${line.benefits.copay_cents != null ? money(line.benefits.copay_cents) : 'not returned'}`,
                                )
                                .join('; ')
                            : !lines.length && reportedOfficeCopay != null
                              ? `${money(reportedOfficeCopay)} specialist office visit · payer-reported`
                              : cardPlanningCopay != null
                                ? `${money(cardPlanningCopay)} specialist office visit · printed on card`
                                : unknown,
      detail: botoxPreview
        ? 'No injection copay was included in this provisional calculation'
        : humanaReferenceCopay != null
          ? 'Published Humana H7617-057 benefit; confirm provider participation and visit conditions before collection'
          : aetnaPlanning
            ? aetnaPlanning.benefit_source.label
            : cignaOfficeScenario
              ? 'Cigna reported this office copay; applicability to the selected diagnostic services is an assumption'
              : cignaFeeProxy && feeProxy?.copay_unconfirmed
                ? 'No CPT copay row was returned; this calculation uses $0'
                : diagnosticCopay != null
                  ? 'One payer diagnostic copay for the selected testing; final share follows the claim'
                  : result.collection_guidance?.basis === 'out_of_pocket_limit'
                    ? 'Plan copay before the applicable out-of-pocket limit; $0 due for covered care'
                    : calculated &&
                        components?.copay_cents === 0 &&
                        !lines.some((line) => line.benefits?.copay_cents != null)
                      ? 'No separate copay was applied to the priced CPTs'
                      : !lines.length && reportedOfficeCopay != null
                        ? 'Payer category term; applicability to planned CPTs and visit date is not confirmed'
                        : cardPlanningCopay != null
                          ? 'Card-based office suggestion; payer applicability and imaging cost are unresolved'
                          : undefined,
    },
    {
      label: 'Individual deductible',
      value:
        humanaReferenceCopay != null && benefits?.deductible_applies === false
          ? '$0.00 annual · no medical deductible (published plan)'
          : feeProxy != null &&
              !cignaFeeProxy &&
              feeProxy.basis !== 'bcbstx_uhc_in_network_fee_proxy'
            ? `${money(feeProxy.deductible_cents)} annual · published group plan`
            : [
                benefits?.deductible_annual_cents != null
                  ? `${money(benefits.deductible_annual_cents)} annual`
                  : null,
                benefits?.deductible_remaining_cents != null
                  ? `${money(benefits.deductible_remaining_cents)} remaining`
                  : null,
              ]
                .filter(Boolean)
                .join(' · ') ||
              (individualDeductible.value !== unknown
                ? individualDeductible.value
                : surgicalIndividualDeductible.value !== unknown
                  ? `${surgicalIndividualDeductible.value} · surgical services`
                  : unknown),
      detail: botoxPreview
        ? 'Individual balance not returned; provisional scenario uses the payer-reported met family deductible'
        : cignaFeeProxy
          ? cignaOfficeScenario
            ? `${money(0)} applied in this provisional scenario from the general individual remaining balance; CPT applicability is not confirmed`
            : `${money(feeProxy?.deductible_cents || 0)} applied in this provisional scenario; exact CPT Stedi responses reported no remaining individual deductible`
          : feeProxy != null
            ? feeProxy.deductible_source
            : result.collection_guidance?.basis === 'out_of_pocket_limit'
              ? individualDeductible.value === unknown && benefits?.deductible_annual_cents == null
                ? 'No deductible payment due after the out-of-pocket limit; the payer did not report an office-specific deductible rule'
                : 'No deductible payment due for this covered office visit after the out-of-pocket limit'
              : calculated && components?.deductible_cents != null
                ? `${money(components.deductible_cents)} applied to this visit`
                : individualDeductible.unassigned
                  ? 'Payer-reported plan amount; medical applicability needs confirmation'
                  : surgicalIndividualDeductible.value !== unknown
                    ? 'Payer did not report an office-specific deductible rule; surgical balance does not establish one'
                    : lines.length &&
                        lines.every((line) => line.benefits?.deductible_applies === false)
                      ? 'Does not apply to the selected CPTs'
                      : 'Office deductible applicability or amount is unresolved for the selected CPTs',
    },
    {
      label: 'Family deductible',
      value:
        humanaReferenceCopay != null
          ? 'Not applicable to this Medicare Advantage individual plan'
          : familyDeductible.value !== unknown
            ? familyDeductible.value
            : surgicalFamilyDeductible.value !== unknown
              ? `${surgicalFamilyDeductible.value} · surgical services`
              : unknown,
      detail: familyOopApplied
        ? 'No deductible payment due for the selected covered services after the family out-of-pocket limit'
        : familyDeductible.unassigned
          ? 'Payer-reported plan amount; medical applicability unverified'
          : surgicalFamilyDeductible.value !== unknown
            ? 'Payer did not report a family office-specific deductible rule; this surgical balance does not establish one'
            : undefined,
    },
    {
      label: 'Coinsurance',
      value: aetnaPlanning
        ? `0% applied · ${money(0)}`
        : combinedPlan
          ? `${combinedPlan.coinsurance_percent}% · ${money(combinedPlan.diagnostic_coinsurance_cents)}`
          : botoxPreview
            ? `${botoxPreview.coinsurance_percent}% · ${money(botoxPreview.patient_share_cents)} provisional`
            : feeProxy != null
              ? `${feeProxy.coinsurance_percent}%${cignaOfficeScenario ? ' assumed' : ''} · ${money(feeProxy.coinsurance_cents)} on the fee proxy`
              : result.collection_guidance?.basis === 'out_of_pocket_limit'
                ? reportedCoinsurance != null
                  ? `${reportedCoinsurance}% reported; $0 payable after out-of-pocket limit`
                  : '$0 payable after out-of-pocket limit'
                : calculated && components?.coinsurance_cents != null
                  ? money(components.coinsurance_cents)
                  : reportedCoinsurance != null
                    ? `${reportedCoinsurance}% reported; dollar amount pending`
                    : humanaReferenceCopay != null
                      ? '40% out of network · in-network office benefit is a copay'
                      : unknown,
      detail: aetnaPlanning
        ? 'The planning formula uses a fixed diagnostic copay rather than office visit coinsurance'
        : combinedPlan
          ? `${combinedPlan.coinsurance_percent}% × ${money(combinedPlan.diagnostic_allowed_cents)} = ${money(combinedPlan.diagnostic_coinsurance_cents)}`
          : botoxPreview
            ? `${botoxPreview.coinsurance_percent}% × ${money(botoxPreview.allowed_cents)} = ${money(botoxPreview.patient_share_cents)}`
            : feeProxy != null
              ? proxyFormula?.coinsurance ||
                `${money(feeProxy.reference_total_cents)} × ${feeProxy.coinsurance_percent}% = ${money(feeProxy.coinsurance_cents)}`
              : calculated
                ? coinsuranceFormula(result) ||
                  (components?.coinsurance_cents === 0
                    ? 'No coinsurance applied after deductible'
                    : undefined)
                : !lines.length && reportedCoinsurance != null
                  ? 'Payer-reported office visit term; diagnostic testing share is not established'
                  : undefined,
    },
    ...(calculated && (components?.allowed_cap_adjustment_cents ?? 0) > 0
      ? [
          {
            label: 'Payer allowed amount reduction',
            value: `−${money(components!.allowed_cap_adjustment_cents!)}`,
          },
        ]
      : []),
    ...commercialOopRows(
      result,
      reportedResult,
      benefits,
      familyOopApplied,
      calculated,
      humanaReferenceCopay,
    ),
    { label: 'Referral', value: requirementStatus(result, 'referral') },
    { label: 'Prior authorization', value: requirementStatus(result, 'authorization') },
    {
      label: 'Cash alternative',
      value:
        automaticCashFallback && model.cashPrice
          ? model.cashPrice
          : price?.cash_complete && price.cash_min_cents != null && price.cash_max_cents != null
            ? range(price.cash_min_cents, price.cash_max_cents)
            : pricedCashLines.length === 1 && /^9920[2-5]$/.test(pricedCashLines[0].code)
              ? `${money(pricedCashLines[0].cash_cents!)} new-patient office visit`
              : 'Not quoted',
      detail: 'Separate self-pay price; not used as the insurance allowed amount',
    },
  ]
  const patientShare = automaticCashFallback
    ? officeReferenceCopay != null || humanaReferenceCopay != null
      ? `${money(officeReferenceCopay ?? humanaReferenceCopay!)} office-only reference; collection pending`
      : cardPlanningCopay != null
        ? `${money(cardPlanningCopay)} office-only card suggestion`
        : 'Not fully calculated'
    : combinedPlan
      ? `${money(combinedPlan.patient_share_cents)} planning estimate`
      : ['qmb_protection', 'out_of_pocket_limit'].includes(
            result.collection_guidance?.basis || '',
          ) && result.collection_guidance?.amount_cents === 0
        ? money(0)
        : calculated &&
            summary?.estimated_total_patient_min_cents != null &&
            summary.estimated_total_patient_max_cents != null
          ? range(
              summary.estimated_total_patient_min_cents,
              summary.estimated_total_patient_max_cents,
            )
          : aetnaPlanning
            ? `${money(aetnaPlanning.patient_share_cents)} planning estimate`
            : botoxPreview
              ? `${money(botoxPreview.patient_share_cents)} provisional · CPT ${botoxPreview.code} scenario`
              : feeProxy != null
                ? `${money(feeProxy.patient_share_cents)} provisional · BCBS Texas fee proxy`
                : humanaReferenceCopay != null
                  ? `${money(humanaReferenceCopay)} conditional in-network office copay · collection pending`
                  : aetnaCopayPlanning != null
                    ? `${money(aetnaCopayPlanning)} provisional office copay`
                    : model.estimatedShare ||
                      (partialOfficeCopay != null
                        ? `${money(model.officeCopayLine!.benefits.copay_cents!)} office-only planning estimate`
                        : cardPlanningCopay != null
                          ? `${money(cardPlanningCopay)} office-only card suggestion`
                          : null) ||
                      (model.provisionalAmount != null
                        ? money(model.provisionalAmount)
                        : model.patientPrice || 'Not fully calculated')
  const rows: CostBreakdownRow[] =
    model.cash && !automaticCashFallback
      ? [
          { label: 'Payment method', value: 'Self-pay' },
          {
            label: 'Planned services',
            value: lines.length
              ? lines
                  .map((line) =>
                    serviceRowLabel({
                      description: line.description,
                      reason: result.reason || model.anticipatedService,
                      code: line.code,
                      units: line.units,
                    }),
                  )
                  .join('; ')
              : result.cash_quote?.package_label || 'Not selected',
          },
          { label: 'Clinic cash price', value: model.cashPrice || 'Not quoted' },
        ]
      : [
          ...insuredRows,
          {
            label:
              cignaFeeProxy || summary?.conditional
                ? 'Conditional patient share'
                : 'Estimated patient share',
            value: patientShare,
            detail: automaticCashFallback
              ? cardPlanningCopay != null
                ? 'Printed specialist copay for one office visit; planned imaging and final payer share are unresolved.'
                : 'Insured share awaits a matching payer check and selected CPTs'
              : combinedPlan
                ? `${money(combinedPlan.office_copay_cents)} office copay + ${combinedPlan.coinsurance_percent}% × ${money(combinedPlan.diagnostic_allowed_cents)} diagnostic allowance. No additional deductible or out-of-pocket reduction was applied where remaining balances were not returned.`
                : result.collection_guidance?.basis === 'out_of_pocket_limit'
                  ? 'Covered in-network share is capped by the applicable out-of-pocket limit'
                  : aetnaPlanning
                    ? `min(${money(aetnaPlanning.copay_cents)} diagnostic copays + ${money(aetnaPlanning.deductible_cents)} deductible + ${money(aetnaPlanning.coinsurance_cents)} coinsurance, ${money(aetnaPlanning.estimated_allowed_cents)} fee reference, ${money(aetnaPlanning.oop_remaining_cents)} remaining out-of-pocket) = ${money(aetnaPlanning.patient_share_cents)} before prior payments. ${aetnaPlanning.message}`
                    : botoxPreview
                      ? `One procedure unit at ${money(botoxPreview.allowed_cents)} from ${botoxPreview.rate_source}; ${botoxPreview.coinsurance_percent}% office-injection coinsurance from Stedi. Confirm coding and individual benefit limits before charging.`
                      : feeProxy != null
                        ? feeProxy.message
                        : aetnaCopayPlanning != null
                          ? `${money(aetnaCopayPlanning)} copay + ${money(0)} deductible + 0% × Aetna allowed amount = ${money(aetnaCopayPlanning)} before the unknown allowance cap. Confirm the visit conditions and final claim amount.`
                          : calculated
                            ? 'Copay + deductible applied + coinsurance − allowed and out-of-pocket reductions'
                            : cardPlanningCopay != null
                              ? 'Printed specialist copay for one office visit; planned imaging and final payer share are unresolved.'
                              : 'Full share awaits applicable deductible and out-of-pocket terms',
          },
          ...(summary?.payments_verified_cents != null && summary.payments_verified_cents > 0
            ? [{ label: 'Already paid', value: `−${money(summary.payments_verified_cents)}` }]
            : []),
        ]
  rows.push(
    collectionAtVisitRow(result, model, {
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
    }),
  )
  return <CommercialCostTables result={result} cash={model.cash} rows={rows} />
}
