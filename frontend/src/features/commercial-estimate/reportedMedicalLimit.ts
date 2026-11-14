import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { patientBenefitsBound } from './identityBinding'

export function reportedMedicalLimit(
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

export function reportedOfficeMedicalLimit(
  result: PrevisitEstimate,
  level: 'INDIVIDUAL' | 'FAMILY',
  period: 'CALENDAR_YEAR' | 'REMAINING',
) {
  const benefits = result.benefits
  const lines = result.price_range?.mode === 'services' ? result.price_range.lines || [] : []
  if (
    benefits?.active !== true ||
    benefits.service_date_matches === false ||
    benefits.network_status !== 'in_network' ||
    !patientBenefitsBound(result.identity) ||
    result.price_range?.complete !== true ||
    lines.length !== 1 ||
    !/^992(?:0[2-5]|1[2-5])$/.test(lines[0].code) ||
    lines[0].units !== 1
  )
    return null
  const rows = (benefits.reported_options || []).filter(
    (row) =>
      row.kind === 'out_of_pocket' &&
      row.service_type_code === '98' &&
      row.coverage_level === level &&
      row.network === 'IN_NETWORK' &&
      row.plan_association === 'medical' &&
      ['CALENDAR_YEAR', 'REMAINING'].includes(row.time_period || '') &&
      (row.payer_note === '' || row.payer_note === 'PHY VISIT - SPECIALIST') &&
      Number.isInteger(row.plan_index) &&
      row.amount_cents != null,
  )
  if (
    !rows.length ||
    new Set(rows.map((row) => JSON.stringify([row.plan_index, row.insurance_type]))).size !== 1
  )
    return null
  const selected = rows.filter((row) => row.time_period === period)
  return selected.length &&
    new Set(selected.map((row) => JSON.stringify([row.amount_cents, row.effective_dates]))).size ===
      1
    ? selected[0].amount_cents
    : null
}
