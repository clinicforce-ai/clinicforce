import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { PrevisitBenefits } from '../../services/api/commercial-benefits'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { money } from './format'
import { limitSummary } from './limitSummary'
import { reportedLimit } from './reportedLimit'
import { reportedMedicalLimit } from './reportedMedicalLimit'

export function commercialOopRows(
  result: PrevisitEstimate,
  reportedResult: PrevisitEstimate,
  benefits: PrevisitBenefits,
  familyOopApplied: boolean,
  calculated: boolean,
  humanaReferenceCopay: number | null,
): CostBreakdownRow[] {
  const individualReportedOop = limitSummary(reportedResult, 'out_of_pocket', 'INDIVIDUAL')
  const reportedIndividualRemainingOop = reportedLimit(
    reportedResult,
    'out_of_pocket',
    'INDIVIDUAL',
    'REMAINING',
  )
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
  const unconfirmedZeroOop =
    reportedIndividualRemainingOop.unassigned && reportedIndividualRemainingOop.amount === money(0)
  const components = result.encounter_summary?.components
  return [
    {
      label: 'Individual out-of-pocket maximum',
      value:
        humanaReferenceCopay != null
          ? `${money(680000)} in-network annual · ${money(1190000)} combined annual (published plan)${individualOopRemaining != null ? ` · ${money(individualOopRemaining)} payer-reported remaining, tier unconfirmed` : ''}`
          : [
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
        humanaReferenceCopay != null
          ? 'Not applicable to this Medicare Advantage individual plan'
          : [
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
  ]
}
