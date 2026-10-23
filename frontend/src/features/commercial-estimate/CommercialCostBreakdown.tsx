import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import CostBreakdownTable, { type CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { serviceRowLabel } from '../insurance-estimate/serviceRowLabel'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { coinsuranceFormula } from './coinsuranceFormula'
import { money, range } from './format'
import { requirementForLine } from './requirementForLine'

const unknown = 'Not returned for this visit'

function familyLimit(
  result: PrevisitEstimate,
  kind: 'deductible' | 'out_of_pocket',
  period: 'CALENDAR_YEAR' | 'REMAINING',
) {
  const amounts = [
    ...new Set(
      (result.benefits?.reported_options || [])
        .filter(
          (row) =>
            row.kind === kind &&
            row.coverage_level === 'FAMILY' &&
            row.time_period === period &&
            row.plan_association === 'medical' &&
            row.amount_cents != null,
        )
        .map((row) => row.amount_cents!),
    ),
  ]
  return amounts.length === 1 ? money(amounts[0]) : unknown
}

function familyLimitSummary(result: PrevisitEstimate, kind: 'deductible' | 'out_of_pocket') {
  const annual = familyLimit(result, kind, 'CALENDAR_YEAR')
  const remaining = familyLimit(result, kind, 'REMAINING')
  if (annual === unknown && remaining === unknown) return unknown
  return `${annual === unknown ? 'Annual amount not returned' : `${annual} annual`} · ${remaining === unknown ? 'Remaining amount not returned' : `${remaining} remaining`}`
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
        : evidence?.status === 'Not required'
          ? 'not required'
          : evidence?.status === 'Required' || lineRule?.required === 'required'
            ? kind === 'authorization'
              ? clearanceOnFile && evidence?.approval_status === 'Verified'
                ? 'approval verified'
                : 'required; approval not verified'
              : clearanceOnFile
                ? 'referral on file'
                : 'required; referral not verified'
            : 'not verified'
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
  const components = summary?.components
  const lineAllowedTotal =
    price?.complete === true &&
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
        price?.complete && price.allowed_min_cents != null && price.allowed_max_cents != null
          ? range(price.allowed_min_cents, price.allowed_max_cents)
          : lineAllowedTotal != null
            ? money(lineAllowedTotal)
            : 'Not fully priced',
    },
    {
      label: 'Copay',
      value:
        calculated && components?.copay_cents != null
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
        calculated &&
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
          .join(' · ') || unknown,
      detail:
        calculated && components?.deductible_cents != null
          ? `${money(components.deductible_cents)} applied to this visit`
          : lines.length && lines.every((line) => line.benefits?.deductible_applies === false)
            ? 'Does not apply to the selected CPTs'
            : 'Applicability to the selected CPTs is not established',
    },
    {
      label: 'Family deductible',
      value: familyLimitSummary(result, 'deductible'),
    },
    {
      label: 'Coinsurance',
      value:
        calculated && components?.coinsurance_cents != null
          ? money(components.coinsurance_cents)
          : benefits?.coinsurance_percent != null
            ? `${benefits.coinsurance_percent}% reported; dollar amount pending`
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
          benefits?.oop_annual_cents != null ? `${money(benefits.oop_annual_cents)} annual` : null,
          benefits?.oop_remaining_cents != null
            ? `${money(benefits.oop_remaining_cents)} remaining`
            : null,
        ]
          .filter(Boolean)
          .join(' · ') || unknown,
      detail:
        calculated && components?.oop_adjustment_cents != null
          ? `${money(components.oop_adjustment_cents)} reduction applied to this visit`
          : 'Payer-reported limit; applicability to these CPTs depends on the completed estimate',
    },
    {
      label: 'Family out-of-pocket maximum',
      value: familyLimitSummary(result, 'out_of_pocket'),
    },
    { label: 'Referral', value: requirementStatus(result, 'referral') },
    { label: 'Prior authorization', value: requirementStatus(result, 'authorization') },
    {
      label: 'Cash alternative',
      value:
        price?.cash_complete && price.cash_min_cents != null && price.cash_max_cents != null
          ? range(price.cash_min_cents, price.cash_max_cents)
          : 'Not quoted',
      detail: 'Separate self-pay price; not used as the insurance allowed amount',
    },
  ]

  const patientShare =
    calculated &&
    summary?.estimated_total_patient_min_cents != null &&
    summary.estimated_total_patient_max_cents != null
      ? range(summary.estimated_total_patient_min_cents, summary.estimated_total_patient_max_cents)
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
          detail: 'Copay + deductible applied + coinsurance − allowed and out-of-pocket reductions',
        },
        ...(summary?.payments_verified_cents != null && summary.payments_verified_cents > 0
          ? [{ label: 'Already paid', value: `−${money(summary.payments_verified_cents)}` }]
          : []),
      ]

  rows.push({
    label: model.supportedAmount ? 'Amount to collect at visit' : 'Collection decision',
    value:
      model.supportedAmount && result.collection_guidance?.amount_cents != null
        ? money(result.collection_guidance.amount_cents)
        : 'Not established',
    detail: model.supportedAmount
      ? result.collection_guidance?.basis === 'office_copay'
        ? 'Office copay advance; other services may have a later balance'
        : result.collection_guidance?.basis === 'claim_after_service'
          ? 'Clinic policy defers collection until after the claim'
          : undefined
      : undefined,
    emphasis: true,
  })
  return <CostBreakdownTable rows={rows} />
}
