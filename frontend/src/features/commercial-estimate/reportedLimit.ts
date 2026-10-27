import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { money } from './format'

export function reportedLimit(
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
  return { amount: 'Not returned for this visit', unassigned: false }
}
