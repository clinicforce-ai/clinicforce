import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { PrevisitBenefits } from '../../services/api/commercial-benefits'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { money, visitDate } from './format'
import { limitSummary } from './limitSummary'
import { reportedLimit } from './reportedLimit'
import { reportedMedicalLimit } from './reportedMedicalLimit'

export function familyOopLimitApplied(result: PrevisitEstimate): boolean {
  const lines = result.price_range?.mode === 'services' ? result.price_range.lines || [] : []
  return (
    result.collection_guidance?.basis === 'out_of_pocket_limit' &&
    lines.length > 0 &&
    lines.every((line) => line.benefits?.family_oop_zero_applies)
  )
}

export function commercialOopRows(
  result: PrevisitEstimate,
  reportedResult: PrevisitEstimate,
  benefits: PrevisitBenefits,
  familyOopApplied: boolean,
  calculated: boolean,
  humanaReferenceCopay: number | null,
): CostBreakdownRow[] {
  const ehr = humanaReferenceCopay == null ? result.ecw_eligibility_reference : undefined
  const individualReportedOop = limitSummary(reportedResult, 'out_of_pocket', 'INDIVIDUAL')
  const reportedIndividualRemainingOop = reportedLimit(
    reportedResult,
    'out_of_pocket',
    'INDIVIDUAL',
    'REMAINING',
  )
  const reportedIndividualAnnual =
    benefits?.oop_annual_cents ??
    reportedMedicalLimit(reportedResult, 'INDIVIDUAL', 'CALENDAR_YEAR')
  const reportedIndividualRemaining =
    benefits?.oop_remaining_cents ?? reportedMedicalLimit(reportedResult, 'INDIVIDUAL', 'REMAINING')
  const individualOopAnnual = reportedIndividualAnnual ?? ehr?.individual_oop_annual_cents
  const individualOopRemaining = reportedIndividualRemaining ?? ehr?.individual_oop_remaining_cents
  const ehrIndividualUsed =
    ehr != null && (reportedIndividualAnnual == null || reportedIndividualRemaining == null)
  const ehrIndividualConflict =
    ehr != null &&
    ((reportedIndividualAnnual != null &&
      reportedIndividualAnnual !== ehr.individual_oop_annual_cents) ||
      (reportedIndividualRemaining != null &&
        reportedIndividualRemaining !== ehr.individual_oop_remaining_cents))
  const reportedOopOnly =
    (benefits?.oop_annual_cents == null && reportedIndividualAnnual != null) ||
    (benefits?.oop_remaining_cents == null && reportedIndividualRemaining != null)
  const reportedFamilyAnnual = reportedMedicalLimit(reportedResult, 'FAMILY', 'CALENDAR_YEAR')
  const reportedFamilyRemaining = reportedMedicalLimit(reportedResult, 'FAMILY', 'REMAINING')
  const familyOopAnnual = reportedFamilyAnnual ?? ehr?.family_oop_annual_cents
  const familyOopRemaining = reportedFamilyRemaining ?? ehr?.family_oop_remaining_cents
  const ehrFamilyUsed =
    ehr != null && (reportedFamilyAnnual == null || reportedFamilyRemaining == null)
  const ehrFamilyConflict =
    ehr != null &&
    ((reportedFamilyAnnual != null && reportedFamilyAnnual !== ehr.family_oop_annual_cents) ||
      (reportedFamilyRemaining != null &&
        reportedFamilyRemaining !== ehr.family_oop_remaining_cents))
  const ehrDetail =
    ehr &&
    `EHR eligibility report dated ${visitDate(ehr.report_date)}. Its figures are reference only and are not used in the patient-share calculation. Recheck visit-date terms.`
  const unconfirmedZeroOop =
    reportedIndividualRemainingOop.unassigned && reportedIndividualRemainingOop.amount === money(0)
  const combinedRows =
    benefits?.active === true && benefits.service_date_matches !== false
      ? (reportedResult.benefits?.reported_options || []).filter(
          (row) =>
            row.kind === 'out_of_pocket' &&
            row.service_type_code === '30' &&
            row.coverage_level === 'INDIVIDUAL' &&
            row.network === 'IN_AND_OUT_OF_NETWORK' &&
            row.plan_association === 'medical' &&
            row.amount_cents != null,
        )
      : []
  const combinedAmount = (period: string) => {
    if (new Set(combinedRows.map((row) => row.plan_index)).size !== 1) return null
    const amounts = [
      ...new Set(
        combinedRows.filter((row) => row.time_period === period).map((row) => row.amount_cents!),
      ),
    ]
    return amounts.length === 1 ? money(amounts[0]) : null
  }
  const combinedAnnual = combinedAmount('CALENDAR_YEAR')
  const combinedRemaining = combinedAmount('REMAINING')
  const components = result.encounter_summary?.components
  const gehaZero =
    result.collection_guidance?.basis === 'published_geha_pshb_zero_office' &&
    result.collection_guidance.amount_cents === 0
  const cignaFamilyOffice =
    result.fee_proxy_estimate?.scenario_kind === 'oap_office_family_deductible'
  return [
    {
      label: 'Individual out-of-pocket maximum',
      value: cignaFamilyOffice
        ? 'Not applicable · family-only accumulator'
        : gehaZero
          ? '$0.00 in-network annual · published plan; Stedi did not return a member out-of-pocket row'
          : humanaReferenceCopay != null
            ? `${money(680000)} in-network annual · ${money(1190000)} combined annual (published plan)${individualOopRemaining != null ? ` · ${money(individualOopRemaining)} payer-reported remaining, tier unconfirmed` : ''}`
            : [
                individualOopAnnual != null
                  ? `${money(individualOopAnnual)} annual${reportedIndividualAnnual == null ? ' (EHR PDF)' : ''}`
                  : null,
                individualOopRemaining != null
                  ? `${money(individualOopRemaining)} remaining${reportedIndividualRemaining == null ? ' (EHR PDF)' : ''}`
                  : null,
              ]
                .filter(Boolean)
                .join(' · ') || individualReportedOop.value,
      detail: cignaFamilyOffice
        ? 'The exact CPT family out-of-pocket balance caps the provisional planning share.'
        : familyOopApplied
          ? 'The individual balance can remain positive after the family limit is met'
          : gehaZero
            ? 'The 2026 G.E.H.A High Medicare Advantage guide lists a $0 in-network medical out-of-pocket maximum for covered care.'
            : ehrIndividualConflict
              ? 'Saved payer evidence and the dated EHR report disagree; verify current limits with the payer before using them.'
              : ehrIndividualUsed
                ? ehrDetail
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
    ...(combinedAnnual || combinedRemaining
      ? [
          {
            label: 'Individual combined in/out-of-network out-of-pocket maximum',
            value: [
              combinedAnnual && `${combinedAnnual} annual`,
              combinedRemaining && `${combinedRemaining} remaining`,
            ]
              .filter(Boolean)
              .join(' · '),
            detail:
              'Payer-reported combined limit; it does not establish provider participation or the amount to collect for this visit.',
          },
        ]
      : []),
    {
      label: 'Family out-of-pocket maximum',
      value: gehaZero
        ? 'Not applicable to this Medicare Advantage individual plan'
        : humanaReferenceCopay != null
          ? 'Not applicable to this Medicare Advantage individual plan'
          : [
              familyOopAnnual != null
                ? `${money(familyOopAnnual)} annual${reportedFamilyAnnual == null ? ' (EHR PDF)' : ''}`
                : null,
              familyOopRemaining != null
                ? `${money(familyOopRemaining)} remaining${reportedFamilyRemaining == null ? ' (EHR PDF)' : ''}`
                : null,
            ]
              .filter(Boolean)
              .join(' · ') || limitSummary(reportedResult, 'out_of_pocket', 'FAMILY').value,
      detail: cignaFamilyOffice
        ? 'The exact CPT family out-of-pocket balance caps the provisional planning share; the final claim may differ.'
        : familyOopApplied
          ? 'Met in-network family medical limit applied to every selected covered CPT'
          : ehrFamilyConflict
            ? 'Saved payer evidence and the dated EHR report disagree; verify current limits with the payer before using them.'
            : ehrFamilyUsed
              ? ehrDetail
              : familyOopAnnual != null || familyOopRemaining != null
                ? 'Payer-reported medical limit; not used as a verified calculation input'
                : limitSummary(reportedResult, 'out_of_pocket', 'FAMILY').unassigned
                  ? 'Payer-reported plan amount; medical applicability unverified and not used as a cap'
                  : undefined,
    },
  ]
}
