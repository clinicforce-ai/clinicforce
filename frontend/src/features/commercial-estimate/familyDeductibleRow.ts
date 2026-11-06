import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { money } from './format'
import { limitSummary, unknown } from './limitSummary'

export function familyDeductibleRow(
  reportedResult: PrevisitEstimate,
  gehaZero: boolean,
  humanaReferenceCopay: number | null,
  familyOopApplied: boolean,
  cignaFamilyDeductibleCents: number | null,
  cignaDeductibleAssumed = false,
): CostBreakdownRow {
  const family = limitSummary(reportedResult, 'deductible', 'FAMILY')
  const surgical = limitSummary(reportedResult, 'deductible', 'FAMILY', '2')
  return {
    label: 'Family deductible',
    value:
      gehaZero || humanaReferenceCopay != null
        ? 'Not applicable to this Medicare Advantage individual plan'
        : family.value !== unknown
          ? family.value
          : surgical.value !== unknown
            ? `${surgical.value} · surgical services`
            : unknown,
    detail: familyOopApplied
      ? 'No deductible payment due for the selected covered services after the family out-of-pocket limit'
      : cignaFamilyDeductibleCents != null
        ? `${money(cignaFamilyDeductibleCents)} ${cignaDeductibleAssumed ? 'assumed to apply' : 'applied'} to the BCBS Texas office fee reference; the actual Cigna allowed amount is unknown`
        : family.unassigned
          ? 'Payer-reported plan amount; medical applicability unverified'
          : surgical.value !== unknown
            ? 'Payer did not report a family office-specific deductible rule; this surgical balance does not establish one'
            : undefined,
  }
}
