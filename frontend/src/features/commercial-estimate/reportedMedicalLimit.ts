import type { PrevisitEstimate } from '../../services/api/commercial-estimator'

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
