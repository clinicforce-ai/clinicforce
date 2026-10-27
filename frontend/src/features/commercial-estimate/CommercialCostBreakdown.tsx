import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import CostBreakdownTable, { type CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { serviceRowLabel } from '../insurance-estimate/serviceRowLabel'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { coinsuranceFormula } from './coinsuranceFormula'
import { money, range } from './format'
import { requirementStatus } from './commercialRequirementStatus'
import {
  aetnaOfficeReferenceCopay,
  specialistOfficeCoinsurance,
  specialistOfficeCopay,
} from './payerReportedPartialCopay'
import { reportedMedicalLimit } from './reportedMedicalLimit'
import { referenceCoverageRow } from './referenceCoverageRow'
import { reportedLimit } from './reportedLimit'
import { limitSummary, unknown } from './limitSummary'

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
  const lines = price?.mode === 'services' ? price.lines || [] : []
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
  const feeProxyCheckin = feeProxy
    ? Math.max(0, feeProxy.patient_share_cents - (summary?.payments_verified_cents || 0))
    : null
  const individualDeductible = limitSummary(reportedResult, 'deductible', 'INDIVIDUAL')
  const surgicalIndividualDeductible = limitSummary(reportedResult, 'deductible', 'INDIVIDUAL', '2')
  const familyDeductible = limitSummary(reportedResult, 'deductible', 'FAMILY')
  const surgicalFamilyDeductible = limitSummary(reportedResult, 'deductible', 'FAMILY', '2')
  const individualReportedOop = limitSummary(reportedResult, 'out_of_pocket', 'INDIVIDUAL')
  const reportedIndividualRemainingOop = reportedLimit(
    reportedResult,
    'out_of_pocket',
    'INDIVIDUAL',
    'REMAINING',
  )
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
  const unconfirmedZeroOop =
    reportedIndividualRemainingOop.unassigned && reportedIndividualRemainingOop.amount === money(0)
  const components = summary?.components
  const diagnosticCopay =
    result.collection_guidance?.basis === 'diagnostic_day_copay' &&
    lines.length > 0 &&
    lines.every((line) => line.benefits?.copay_cents === lines[0].benefits?.copay_cents)
      ? lines[0].benefits?.copay_cents
      : null
  const individualOopAnnual =
    benefits?.oop_annual_cents ??
    reportedMedicalLimit(reportedResult, 'INDIVIDUAL', 'CALENDAR_YEAR')
  const individualOopRemaining =
    benefits?.oop_remaining_cents ?? reportedMedicalLimit(reportedResult, 'INDIVIDUAL', 'REMAINING')
  const reportedOopOnly =
    (benefits?.oop_annual_cents == null && individualOopAnnual != null) ||
    (benefits?.oop_remaining_cents == null && individualOopRemaining != null)
  const familyOopAnnual = reportedMedicalLimit(reportedResult, 'FAMILY', 'CALENDAR_YEAR')
  const familyOopRemaining = reportedMedicalLimit(reportedResult, 'FAMILY', 'REMAINING')
  const pricedCashLines = lines.filter((line) => line.cash_cents != null && !line.cash_included)
  const lineAllowedTotal =
    lines.length > 0 &&
    lines.every(
      (line) =>
        line.rate_resolution?.status === 'RESOLVED' &&
        line.allowed_cents != null &&
        ['clinic_contract', 'payer_out_of_network', 'cms_original_medicare_estimate'].includes(
          line.allowed_basis || '',
        ),
    )
      ? lines.reduce((sum, line) => sum + line.allowed_cents!, 0)
      : null
  const calculated =
    !automaticCashFallback &&
    !model.previousEstimate &&
    !summary?.integrity_error &&
    price?.complete === true &&
    (summary?.complete === true || model.supportedAmount === true)
  const insuredRows: CostBreakdownRow[] = [
    referenceCoverageRow(result, model, benefits),
    { label: 'Network', value: model.networkLabel },
    {
      label: result.reason ? 'Reason for visit' : 'Planned service',
      value: visitReason,
    },
    {
      label:
        result.service_selection_status === 'preview' && result.service_plan?.method === 'llm'
          ? 'LLM-proposed CPTs'
          : 'CPTs',
      value: lines.length
        ? lines
            .map(
              (line) =>
                `CPT ${line.code} × ${line.units || 1}${line.modifiers?.length ? ` · modifier ${line.modifiers.join(', ')}` : ''}`,
            )
            .join('; ')
        : 'No CPTs selected',
      detail:
        result.service_selection_status !== 'approved'
          ? result.service_plan?.method === 'llm'
            ? 'Model proposal used for this estimate; billed codes follow the services actually performed'
            : 'Provisional service scenario used for this estimate'
          : undefined,
    },
    ...(lines.length
      ? lines.map((line) => {
          const proxyRate =
            feeProxy &&
            result.temporary_rate_reference?.lines.find(
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
                  ? `${money(proxyRate.unit_reference_cents * (Number(line.units) || 1))} BCBS Texas reference`
                  : 'Allowed amount unavailable',
            detail:
              resolved && line.unit_allowed_cents != null
                ? `${money(line.unit_allowed_cents)} per unit × ${line.units || 1}`
                : proxyRate
                  ? `${money(proxyRate.unit_reference_cents)} per unit × ${line.units || 1}; UHC allowed amount unknown`
                  : line.description || undefined,
          }
        })
      : [{ label: 'Planned CPTs', value: 'No priced CPTs selected' }]),
    {
      label: 'Total payer allowed',
      value: automaticCashFallback
        ? 'Not established'
        : price?.allowed_min_cents != null && price.allowed_max_cents != null
          ? range(price.allowed_min_cents, price.allowed_max_cents)
          : lineAllowedTotal != null
            ? money(lineAllowedTotal)
            : 'Not fully priced',
    },
    ...(result.temporary_rate_reference?.lines.length
      ? [
          {
            label: 'Blue Cross fee reference',
            value: result.temporary_rate_reference.lines
              .map((line) => `CPT ${line.code}: ${money(line.unit_reference_cents)} per unit`)
              .join('; '),
            detail: result.temporary_rate_reference.message,
          },
        ]
      : []),
    ...(feeProxy
      ? [
          {
            label: 'BCBS Texas fee proxy total',
            value: money(feeProxy.reference_total_cents),
            detail:
              'Planning rate for the selected CPTs and units; UHC allowed amount remains unknown',
          },
        ]
      : []),
    {
      label: 'Copay',
      value: familyOopApplied
        ? '$0 payable after family out-of-pocket limit'
        : feeProxy != null
          ? `${money(feeProxy.copay_cents)} diagnostic copay`
          : officeReferenceCopay != null
            ? `${money(officeReferenceCopay)} specialist office visit · payer-reported reference`
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
                    : unknown,
      detail:
        result.collection_guidance?.basis === 'diagnostic_day_copay'
          ? 'One payer diagnostic copay for the selected testing; final share follows the claim'
          : result.collection_guidance?.basis === 'out_of_pocket_limit'
            ? 'Plan copay before the applicable out-of-pocket limit; $0 due for covered care'
            : calculated &&
                components?.copay_cents === 0 &&
                !lines.some((line) => line.benefits?.copay_cents != null)
              ? 'No separate copay was applied to the priced CPTs'
              : !lines.length && reportedOfficeCopay != null
                ? 'Payer category term; applicability to planned CPTs and visit date is not confirmed'
                : undefined,
    },
    {
      label: 'Individual deductible',
      value:
        feeProxy != null
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
      detail:
        feeProxy != null
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
        familyDeductible.value !== unknown
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
      value:
        feeProxy != null
          ? `${feeProxy.coinsurance_percent}% · ${money(feeProxy.coinsurance_cents)} on the fee proxy`
          : result.collection_guidance?.basis === 'out_of_pocket_limit'
            ? reportedCoinsurance != null
              ? `${reportedCoinsurance}% reported; $0 payable after out-of-pocket limit`
              : '$0 payable after out-of-pocket limit'
            : calculated && components?.coinsurance_cents != null
              ? money(components.coinsurance_cents)
              : reportedCoinsurance != null
                ? `${reportedCoinsurance}% reported; dollar amount pending`
                : unknown,
      detail:
        feeProxy != null
          ? `${money(feeProxy.reference_total_cents)} × ${feeProxy.coinsurance_percent}% = ${money(feeProxy.coinsurance_cents)}`
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
    {
      label: 'Individual out-of-pocket maximum',
      value:
        [
          individualOopAnnual != null ? `${money(individualOopAnnual)} annual` : null,
          individualOopRemaining != null ? `${money(individualOopRemaining)} remaining` : null,
        ]
          .filter(Boolean)
          .join(' · ') || individualReportedOop.value,
      detail: familyOopApplied
        ? 'The individual balance can remain positive after the family limit is met'
        : reportedOopOnly
          ? 'Payer-reported medical limit; not used as a verified calculation input'
          : benefits?.oop_remaining_cents === 0
            ? 'Applicable in-network limit has $0 remaining for the selected covered care'
            : calculated && components?.oop_adjustment_cents != null
              ? `${money(components.oop_adjustment_cents)} reduction applied to this visit`
              : unconfirmedZeroOop
                ? 'Payer reports $0 remaining; confirm this is the applicable medical limit before collection'
                : individualReportedOop.unassigned
                  ? 'Payer-reported plan amount; medical applicability needs confirmation before use as a cap'
                  : 'Payer-reported limit; applicability to these CPTs depends on the completed estimate',
    },
    {
      label: 'Family out-of-pocket maximum',
      value:
        [
          familyOopAnnual != null ? `${money(familyOopAnnual)} annual` : null,
          familyOopRemaining != null ? `${money(familyOopRemaining)} remaining` : null,
        ]
          .filter(Boolean)
          .join(' · ') || limitSummary(reportedResult, 'out_of_pocket', 'FAMILY').value,
      detail: familyOopApplied
        ? 'Met in-network family medical limit applied to every selected covered CPT'
        : familyOopAnnual != null || familyOopRemaining != null
          ? 'Payer-reported medical limit; not used as a verified calculation input'
          : limitSummary(reportedResult, 'out_of_pocket', 'FAMILY').unassigned
            ? 'Payer-reported plan amount; medical applicability unverified and not used as a cap'
            : undefined,
    },
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
    ? officeReferenceCopay != null
      ? `${money(officeReferenceCopay)} office-only reference; other services excluded`
      : 'Not fully calculated'
    : result.collection_guidance?.basis === 'qmb_protection' &&
        result.collection_guidance.amount_cents === 0
      ? money(0)
      : result.collection_guidance?.basis === 'out_of_pocket_limit' &&
          result.collection_guidance.amount_cents === 0
        ? money(0)
        : calculated &&
            summary?.estimated_total_patient_min_cents != null &&
            summary.estimated_total_patient_max_cents != null
          ? range(
              summary.estimated_total_patient_min_cents,
              summary.estimated_total_patient_max_cents,
            )
          : feeProxy != null
            ? `${money(feeProxy.patient_share_cents)} provisional · BCBS Texas fee proxy`
            : model.estimatedShare ||
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
            label: summary?.conditional ? 'Conditional patient share' : 'Estimated patient share',
            value: patientShare,
            detail: automaticCashFallback
              ? 'Insured share awaits a matching payer check and selected CPTs'
              : result.collection_guidance?.basis === 'out_of_pocket_limit'
                ? 'Covered in-network share is capped by the applicable out-of-pocket limit'
                : feeProxy != null
                  ? feeProxy.message
                  : calculated
                    ? 'Copay + deductible applied + coinsurance − allowed and out-of-pocket reductions'
                    : 'Full share awaits applicable deductible and out-of-pocket terms',
          },
          ...(summary?.payments_verified_cents != null && summary.payments_verified_cents > 0
            ? [{ label: 'Already paid', value: `−${money(summary.payments_verified_cents)}` }]
            : []),
        ]

  rows.push({
    label:
      feeProxyCheckin != null &&
      !['bcbstx_proxy_advance', 'qmb_protection'].includes(result.collection_guidance?.basis || '')
        ? 'Planned amount to collect at visit'
        : 'Amount to collect at visit',
    value:
      result.collection_guidance?.basis === 'qmb_protection' &&
      result.collection_guidance.amount_cents === 0
        ? money(0)
        : !automaticCashFallback &&
            model.supportedAmount &&
            result.collection_guidance?.amount_cents != null
          ? money(result.collection_guidance.amount_cents)
          : feeProxyCheckin != null
            ? money(feeProxyCheckin)
            : officeReferenceCopay != null
              ? `${money(officeReferenceCopay)} office-only reference`
              : 'Not established',
    detail:
      result.collection_guidance?.basis === 'qmb_protection'
        ? 'QMB protection prohibits collecting Medicare cost sharing for covered care'
        : feeProxy != null
          ? result.collection_guidance?.basis === 'bcbstx_proxy_advance'
            ? 'Clinic-approved BCBS Texas fee proxy advance after Medicare billing-protection clearance; final responsibility follows the claim.'
            : 'Estimated check-in amount from the BCBS Texas fee proxy. Charge only when the clinic collection policy and Medicare billing-protection check clear this member.'
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
  })
  return <CostBreakdownTable rows={rows} />
}
