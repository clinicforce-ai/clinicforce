import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { money } from './format'

export function officeOnlyAmount(
  result: PrevisitEstimate | null | undefined,
  coverageIssue: string | null,
  memberAndTierConfirmed: boolean,
) {
  const guidance = result?.collection_guidance
  const benefits = result?.benefits
  const office = result?.price_range?.lines?.find(
    (line) => line.id === guidance?.office_only_line_id && /^992(?:0[2-5]|1[2-5])$/.test(line.code),
  )
  return (
    guidance?.scope === 'office_only' &&
    guidance.basis === 'office_copay' &&
    !!office &&
    guidance.office_visit_copay_advance?.amount_cents === guidance.amount_cents &&
    result?.encounter_summary?.collect_today_cents === guidance.amount_cents &&
    !result?.encounter_summary?.integrity_error &&
    memberAndTierConfirmed &&
    benefits?.active === true &&
    benefits.service_date_matches !== false &&
    !benefits.has_secondary &&
    result?.recovery?.reason_code !== 'multiple_active_policies' &&
    !coverageIssue &&
    !benefits?.historical_only &&
    !benefits?.source_errors?.length &&
    !benefits?.network_conflict &&
    !office.benefits.source_errors?.length &&
    !['conflict', 'mismatch'].includes(result?.card_check?.status || '') &&
    result?.card_check?.independent_ecw_check !== true &&
    result?.billing_protection?.status !== 'qmb'
  )
}

export function officeOnlyCollectionRow(
  result: PrevisitEstimate,
  supported: boolean,
): CostBreakdownRow | null {
  const guidance = result.collection_guidance
  if (!supported || guidance?.scope !== 'office_only' || guidance.amount_cents == null) return null
  return {
    label: 'Amount to collect at visit · office only',
    value: money(guidance.amount_cents),
    detail: [guidance.reason, ...(guidance.unresolved_service_issues || [])].join(' '),
    emphasis: true,
  }
}
