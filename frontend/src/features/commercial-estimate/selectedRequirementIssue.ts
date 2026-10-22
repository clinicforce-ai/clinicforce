import type { PrevisitEstimate } from '../../services/api/commercial-previsit'
import { requirementForLine } from './requirementForLine'

export function selectedRequirementIssue(
  result: PrevisitEstimate | null | undefined,
): string | null {
  if (!result || result.price_range?.mode !== 'services') return null
  for (const line of result.price_range.lines || []) {
    const decision = requirementForLine(result.requirement_evidence?.procedures, line.id, line.code)
    for (const kind of ['referral', 'authorization'] as const) {
      const label = kind === 'referral' ? 'referral' : 'prior authorization'
      const lineStatus = line.benefits?.requirements?.[kind]?.status?.toLowerCase()
      const approval =
        kind === 'authorization'
          ? (decision?.authorization?.pa_approval ??
            result.requirement_evidence?.authorization?.pa_approval)
          : undefined
      if (
        ['denied', 'expired'].includes(lineStatus || '') ||
        ['DENIED', 'EXPIRED'].includes(approval || '')
      ) {
        return `The ${label} for CPT ${line.code} was denied or expired. Check the payer decision before collecting.`
      }
    }
  }
  return null
}
