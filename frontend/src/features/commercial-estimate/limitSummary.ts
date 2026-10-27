import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { reportedLimit } from './reportedLimit'

export const unknown = 'Not returned for this visit'

export function limitSummary(
  result: PrevisitEstimate,
  kind: 'deductible' | 'out_of_pocket',
  level: 'INDIVIDUAL' | 'FAMILY',
  serviceTypeCode = '30',
) {
  const annual = reportedLimit(result, kind, level, 'CALENDAR_YEAR', serviceTypeCode)
  const remaining = reportedLimit(result, kind, level, 'REMAINING', serviceTypeCode)
  return {
    value:
      annual.amount === unknown && remaining.amount === unknown
        ? unknown
        : `${annual.amount === unknown ? 'Annual amount not returned' : `${annual.amount} annual`} · ${remaining.amount === unknown ? 'Remaining amount not returned' : `${remaining.amount} remaining`}`,
    unassigned: annual.unassigned || remaining.unassigned,
  }
}
