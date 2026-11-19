import type { PrevisitEstimate } from '../../services/api/commercial-estimator'

// A visible planning figure, never a collection authorization while policy evidence conflicts.
export function uhcLppoOfficeScenario(result: PrevisitEstimate) {
  const benefits = result.benefits
  const line =
    result.price_range?.mode === 'services' && result.price_range.lines?.length === 1
      ? result.price_range.lines[0]
      : null
  const terms = line?.benefits
  const plan = result.plan_details?.stedi?.names || benefits?.plan_details?.names || []
  const percent = Number(terms?.coinsurance_percent)
  const fee = line?.allowed_cents
  const identity = result.identity || benefits?.identity
  const dates = benefits?.coverage_evidence
  const visitDateBound =
    benefits?.service_date_matches === true ||
    (benefits?.service_date_matches == null &&
      dates?.status === 'ACTIVE' &&
      !!dates.start &&
      dates.start <= result.service_date &&
      (!dates.end || result.service_date <= dates.end))
  if (
    result.payment_mode === 'cash' ||
    result.collection_guidance?.amount_cents != null ||
    result.billing_note?.kind === 'no_collection' ||
    result.billing_protection?.status === 'qmb' ||
    result.recovery?.reason_code !== 'same_policy_evidence_conflict' ||
    result.card_check?.status === 'conflict' ||
    benefits?.active !== true ||
    !visitDateBound ||
    benefits.coverage_status_conflict ||
    benefits.historical_only ||
    benefits.coverage_dates_invalid ||
    benefits.network_conflict ||
    benefits.has_secondary ||
    benefits.qmb_status === 'protected' ||
    benefits.source_errors?.length ||
    identity?.match_version !== 2 ||
    !identity.binding ||
    !['matched', 'reconciled', 'warning'].includes(identity.status || '') ||
    (identity.status === 'warning' && result.identity?.policy_differences?.length) ||
    !/united\s*health\s*care/i.test(result.payer || '') ||
    plan.length !== 1 ||
    !/^LPPO-UNITEDHEALTHCARE GROUP MEDICARE ADVANTAGE/i.test(plan[0]) ||
    !line ||
    !/^992(?:0[2-5]|1[2-5])$/.test(line.code) ||
    line.units !== 1 ||
    line.modifiers?.length ||
    line.allowed_basis !== 'medicare_fee_schedule' ||
    line.rate_resolution?.status !== 'RESOLVED' ||
    !Number.isInteger(fee) ||
    fee! <= 0 ||
    terms?.active !== true ||
    terms.network_independent !== true ||
    terms.copay_cents !== 0 ||
    terms.deductible_remaining_cents !== 0 ||
    !Number.isFinite(percent) ||
    percent <= 0 ||
    percent > 100 ||
    !Number.isInteger(terms.oop_remaining_cents) ||
    terms.oop_remaining_cents! < 0 ||
    terms.source_errors?.length ||
    result.price_range?.service_issues?.length
  )
    return null
  const share = Math.min(Math.round((fee! * percent) / 100), terms.oop_remaining_cents!)
  const paid = result.encounter_summary?.payments_verified_cents || 0
  if (!Number.isInteger(paid) || paid < 0) return null
  return { fee: fee!, percent, share, balance: Math.max(0, share - paid), paid }
}
