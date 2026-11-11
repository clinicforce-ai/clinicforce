import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { money } from './format'
import { limitSummary, unknown } from './limitSummary'
import type { bluecardOfficeDeductibleScenario } from './bluecardOfficeDeductibleScenario'

export function familyDeductibleRow(
  reportedResult: PrevisitEstimate,
  gehaZero: boolean,
  humanaReferenceCopay: number | null,
  familyOopApplied: boolean,
  bluecard: ReturnType<typeof bluecardOfficeDeductibleScenario>,
  feeProxy: PrevisitEstimate['fee_proxy_estimate'],
): CostBreakdownRow {
  const family = limitSummary(reportedResult, 'deductible', 'FAMILY')
  const surgical = limitSummary(reportedResult, 'deductible', 'FAMILY', '2')
  const cignaFamily = feeProxy?.scenario_kind === 'oap_office_family_deductible'
  const applied = bluecard?.familyDeductible
    ? bluecard.deductible
    : cignaFamily
      ? feeProxy.deductible_cents
      : null
  const assumed =
    bluecard?.familyDeductible || (cignaFamily && feeProxy.deductible_applicability === 'assumed')
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
      : applied != null
        ? `${money(applied)} ${assumed ? 'assumed to apply' : 'applied'} to the BCBS Texas office fee reference; the actual payer allowed amount is unknown`
        : family.unassigned
          ? 'Payer-reported plan amount; medical applicability unverified'
          : surgical.value !== unknown
            ? 'Payer did not report a family office-specific deductible rule; this surgical balance does not establish one'
            : undefined,
  }
}

export function choicePlusDeductibleRows(
  proxy: PrevisitEstimate['fee_proxy_estimate'],
): CostBreakdownRow[] {
  return proxy?.basis === 'bcbstx_uhc_choice_plus_fee_proxy'
    ? [
        {
          label: 'Deductible applied to this scenario',
          value: money(proxy.deductible_cents),
          detail:
            'Applied before coinsurance to the BCBSTX fee reference; CPT-specific applicability is assumed.',
        },
      ]
    : []
}
