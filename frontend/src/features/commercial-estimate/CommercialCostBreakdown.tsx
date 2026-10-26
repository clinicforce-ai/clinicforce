import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import CostBreakdownTable, { type CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { serviceRowLabel } from '../insurance-estimate/serviceRowLabel'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { coinsuranceFormula } from './coinsuranceFormula'
import { money, range } from './format'
import { requirementForLine } from './requirementForLine'

const unknown = 'Not returned for this visit'

function reportedMedicalLimit(
  result: PrevisitEstimate,
  level: 'INDIVIDUAL' | 'FAMILY',
  period: 'CALENDAR_YEAR' | 'REMAINING',
) {
  const benefits = result.benefits
  if (
    benefits?.active !== true ||
    benefits.service_date_matches !== true ||
    benefits.network_status !== 'in_network'
  )
    return null
  const options = benefits.reported_options || []
  const anchors = options.filter(
    (row) =>
      row.kind === 'out_of_pocket' &&
      row.service_type_code === '30' &&
      row.coverage_level === level &&
      row.time_period === 'CALENDAR_YEAR' &&
      row.network === 'IN_NETWORK' &&
      /\bINT MED AND RX\b/i.test(row.payer_note || ''),
  )
  if (anchors.length !== 1) return null
  const amounts = [
    ...new Set(
      options
        .filter(
          (row) =>
            row.kind === 'out_of_pocket' &&
            row.service_type_code === '30' &&
            row.coverage_level === level &&
            row.time_period === period &&
            row.network === 'IN_NETWORK' &&
            row.plan_index === anchors[0].plan_index &&
            row.plan_association !== 'supplementary' &&
            row.amount_cents != null,
        )
        .map((row) => row.amount_cents!),
    ),
  ]
  return amounts.length === 1 ? amounts[0] : null
}

function reportedLimit(
  result: PrevisitEstimate,
  kind: 'deductible' | 'out_of_pocket',
  level: 'INDIVIDUAL' | 'FAMILY',
  period: 'CALENDAR_YEAR' | 'REMAINING',
  serviceTypeCode = '30',
) {
  const rows = (result.benefits?.reported_options || []).filter(
    (row) =>
      row.kind === kind &&
      row.coverage_level === level &&
      row.time_period === period &&
      row.service_type_code === serviceTypeCode &&
      row.amount_cents != null &&
      row.network === 'IN_NETWORK',
  )
  for (const association of ['medical', 'unassigned']) {
    const amounts = [
      ...new Set(
        rows.filter((row) => row.plan_association === association).map((row) => row.amount_cents!),
      ),
    ]
    if (amounts.length === 1)
      return { amount: money(amounts[0]), unassigned: association === 'unassigned' }
    if (amounts.length > 1) break
  }
  return { amount: unknown, unassigned: false }
}

function limitSummary(
  result: PrevisitEstimate,
  kind: 'deductible' | 'out_of_pocket',
  level: 'INDIVIDUAL' | 'FAMILY',
  serviceTypeCode = '30',
) {
  const annual = reportedLimit(result, kind, level, 'CALENDAR_YEAR', serviceTypeCode)
  const remaining = reportedLimit(result, kind, level, 'REMAINING', serviceTypeCode)
  return {
    value:
      annual.amount === unknown && remaining.amount === unknown
        ? unknown
        : `${annual.amount === unknown ? 'Annual amount not returned' : `${annual.amount} annual`} · ${remaining.amount === unknown ? 'Remaining amount not returned' : `${remaining.amount} remaining`}`,
    unassigned: annual.unassigned || remaining.unassigned,
  }
}

function requirementStatus(result: PrevisitEstimate, kind: 'referral' | 'authorization') {
  const lines = result.price_range?.mode === 'services' ? result.price_range.lines || [] : []
  if (!lines.length) return 'Select planned CPTs to check'
  return lines
    .map((line) => {
      const evidence = requirementForLine(
        result.requirement_evidence?.procedures,
        line.id,
        line.code,
      )?.[kind]
      const lineRule = line.benefits?.requirements?.[kind]
      const conflict =
        (evidence?.status === 'Required' && lineRule?.required === 'not_required') ||
        (evidence?.status === 'Not required' && lineRule?.required === 'required')
      const clearanceOnFile =
        lineRule?.status === 'valid' &&
        lineRule.cleared &&
        lineRule.reference &&
        lineRule.verified_at
      const status = conflict
        ? 'conflicting rules'
        : evidence?.status === 'Not required' || lineRule?.required === 'not_required'
          ? 'not required'
          : evidence?.status === 'Required' || lineRule?.required === 'required'
            ? kind === 'authorization'
              ? clearanceOnFile && evidence?.approval_status === 'Verified'
                ? 'approval verified'
                : 'required; approval status unresolved'
              : clearanceOnFile
                ? 'referral on file'
                : 'required; referral status unresolved'
            : 'not determined'
      return `CPT ${line.code}: ${status}`
    })
    .join('; ')
}

export default function CommercialCostBreakdown({
  model,
  result,
}: {
  model: CommercialEstimateModel
  result: PrevisitEstimate
}) {
  const benefits = result.benefits
  const price = result.price_range
  const lines = price?.mode === 'services' ? price.lines || [] : []
  const summary = result.encounter_summary
  const individualDeductible = limitSummary(result, 'deductible', 'INDIVIDUAL')
  const surgicalIndividualDeductible = limitSummary(result, 'deductible', 'INDIVIDUAL', '2')
  const familyDeductible = limitSummary(result, 'deductible', 'FAMILY')
  const surgicalFamilyDeductible = limitSummary(result, 'deductible', 'FAMILY', '2')
  const individualReportedOop = limitSummary(result, 'out_of_pocket', 'INDIVIDUAL')
  const reportedIndividualRemainingOop = reportedLimit(
    result,
    'out_of_pocket',
    'INDIVIDUAL',
    'REMAINING',
  )
  const reportedCoinsurance =
    benefits?.coinsurance_percent ?? lines[0]?.benefits?.coinsurance_percent
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
    benefits?.oop_annual_cents ?? reportedMedicalLimit(result, 'INDIVIDUAL', 'CALENDAR_YEAR')
  const individualOopRemaining =
    benefits?.oop_remaining_cents ?? reportedMedicalLimit(result, 'INDIVIDUAL', 'REMAINING')
  const reportedOopOnly =
    (benefits?.oop_annual_cents == null && individualOopAnnual != null) ||
    (benefits?.oop_remaining_cents == null && individualOopRemaining != null)
  const familyOopAnnual = reportedMedicalLimit(result, 'FAMILY', 'CALENDAR_YEAR')
  const familyOopRemaining = reportedMedicalLimit(result, 'FAMILY', 'REMAINING')
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
    !model.previousEstimate &&
    !summary?.integrity_error &&
    price?.complete === true &&
    (summary?.complete === true || model.supportedAmount === true)
  const insuredRows: CostBreakdownRow[] = [
    {
      label: 'Coverage',
      value: [result.payer || 'Payer not recorded', model.planName].filter(Boolean).join(' · '),
      detail: model.policyStatus,
    },
    { label: 'Network', value: model.networkLabel },
    {
      label: result.reason ? 'Reason for visit' : 'Planned service',
      value: result.reason || model.anticipatedService || 'Not recorded',
    },
    ...(lines.length
      ? lines.map((line) => {
          const resolved =
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
                : 'Allowed amount unavailable',
            detail:
              resolved && line.unit_allowed_cents != null
                ? `${money(line.unit_allowed_cents)} per unit × ${line.units || 1}`
                : line.description || undefined,
          }
        })
      : [{ label: 'Planned CPTs', value: 'No priced CPTs selected' }]),
    {
      label: 'Total payer allowed',
      value:
        price?.allowed_min_cents != null && price.allowed_max_cents != null
          ? range(price.allowed_min_cents, price.allowed_max_cents)
          : lineAllowedTotal != null
            ? money(lineAllowedTotal)
            : 'Not fully priced',
    },
    {
      label: 'Copay',
      value:
        diagnosticCopay != null
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
              : undefined,
    },
    {
      label: 'Individual deductible',
      value:
        [
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
        result.collection_guidance?.basis === 'out_of_pocket_limit'
          ? individualDeductible.value === unknown && benefits?.deductible_annual_cents == null
            ? 'No deductible payment due after the out-of-pocket limit; the payer did not report an office-specific deductible rule'
            : 'No deductible payment due for this covered office visit after the out-of-pocket limit'
          : calculated && components?.deductible_cents != null
            ? `${money(components.deductible_cents)} applied to this visit`
            : individualDeductible.unassigned
              ? 'Payer-reported plan amount; medical applicability needs confirmation'
              : surgicalIndividualDeductible.value !== unknown
                ? 'Payer did not report an office-specific deductible rule; surgical balance does not establish one'
                : lines.length && lines.every((line) => line.benefits?.deductible_applies === false)
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
      detail: familyDeductible.unassigned
        ? 'Payer-reported plan amount; medical applicability unverified'
        : surgicalFamilyDeductible.value !== unknown
          ? 'Payer did not report a family office-specific deductible rule; this surgical balance does not establish one'
          : undefined,
    },
    {
      label: 'Coinsurance',
      value:
        result.collection_guidance?.basis === 'out_of_pocket_limit'
          ? reportedCoinsurance != null
            ? `${reportedCoinsurance}% reported; $0 payable after out-of-pocket limit`
            : '$0 payable after out-of-pocket limit'
          : calculated && components?.coinsurance_cents != null
            ? money(components.coinsurance_cents)
            : benefits?.coinsurance_percent != null
              ? `${benefits.coinsurance_percent}% reported; dollar amount pending`
              : lines.length && lines.every((line) => line.benefits?.coinsurance_percent === '0')
                ? '0% reported for selected CPTs'
                : unknown,
      detail: calculated
        ? coinsuranceFormula(result) ||
          (components?.coinsurance_cents === 0
            ? 'No coinsurance applied after deductible'
            : undefined)
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
      detail: reportedOopOnly
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
          .join(' · ') || limitSummary(result, 'out_of_pocket', 'FAMILY').value,
      detail:
        familyOopAnnual != null || familyOopRemaining != null
          ? 'Payer-reported medical limit; not used as a verified calculation input'
          : limitSummary(result, 'out_of_pocket', 'FAMILY').unassigned
            ? 'Payer-reported plan amount; medical applicability unverified and not used as a cap'
            : undefined,
    },
    { label: 'Referral', value: requirementStatus(result, 'referral') },
    { label: 'Prior authorization', value: requirementStatus(result, 'authorization') },
    {
      label: 'Cash alternative',
      value:
        price?.cash_complete && price.cash_min_cents != null && price.cash_max_cents != null
          ? range(price.cash_min_cents, price.cash_max_cents)
          : pricedCashLines.length === 1 && /^9920[2-5]$/.test(pricedCashLines[0].code)
            ? `${money(pricedCashLines[0].cash_cents!)} new-patient office visit`
            : 'Not quoted',
      detail: 'Separate self-pay price; not used as the insurance allowed amount',
    },
  ]

  const patientShare =
    result.collection_guidance?.basis === 'out_of_pocket_limit' &&
    result.collection_guidance.amount_cents === 0
      ? money(0)
      : calculated &&
          summary?.estimated_total_patient_min_cents != null &&
          summary.estimated_total_patient_max_cents != null
        ? range(
            summary.estimated_total_patient_min_cents,
            summary.estimated_total_patient_max_cents,
          )
        : model.estimatedShare ||
          (model.provisionalAmount != null
            ? money(model.provisionalAmount)
            : model.patientPrice || 'Not fully calculated')
  const rows: CostBreakdownRow[] = model.cash
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
          detail:
            result.collection_guidance?.basis === 'out_of_pocket_limit'
              ? 'Covered in-network share is capped by the applicable out-of-pocket limit'
              : calculated
                ? 'Copay + deductible applied + coinsurance − allowed and out-of-pocket reductions'
                : 'Full share awaits applicable deductible and out-of-pocket terms',
        },
        ...(summary?.payments_verified_cents != null && summary.payments_verified_cents > 0
          ? [{ label: 'Already paid', value: `−${money(summary.payments_verified_cents)}` }]
          : []),
      ]

  rows.push({
    label: 'Amount to collect at visit',
    value:
      model.supportedAmount && result.collection_guidance?.amount_cents != null
        ? money(result.collection_guidance.amount_cents)
        : 'Not established',
    detail: result.collection_guidance?.unconfirmed_zero_oop
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
