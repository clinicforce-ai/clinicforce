import type { PrevisitEstimate, VisitCode } from '../../services/api/commercial-estimator'
import { requirementForLine } from './requirementForLine'

export const requirementTag = (
  result: PrevisitEstimate,
  row: VisitCode,
  kind: 'referral' | 'authorization',
) => {
  const evidence = requirementForLine(
    [...(result.requirement_evidence?.procedures || []), ...(result.candidate_requirements || [])],
    row.id,
    row.code,
  )?.[kind]
  const line = result.price_range?.lines?.find((item) => item.id === row.id)
  const recorded = line?.benefits?.requirements?.[kind]
  const name = kind === 'referral' ? 'Referral' : 'Prior auth'
  if (
    (evidence?.status === 'Required' && recorded?.required === 'not_required') ||
    (evidence?.status === 'Not required' && recorded?.required === 'required')
  )
    return { label: `${name}: review rule`, tone: 'unknown' }
  if (evidence?.status === 'Required' || recorded?.required === 'required') {
    const recordedClearance =
      recorded?.cleared && recorded.status === 'valid' && recorded.reference && recorded.verified_at
    const approvalStatus =
      evidence?.approval_status ?? result.requirement_evidence?.authorization?.approval_status
    const verified = recordedClearance && (kind === 'referral' || approvalStatus === 'Verified')
    const authorizationOnFile = kind === 'authorization' && recordedClearance && !approvalStatus
    return {
      label: verified
        ? `${name} on file`
        : authorizationOnFile
          ? `${name} reference on file`
          : `${name} required`,
      tone: verified ? 'clear' : authorizationOnFile ? 'unknown' : 'required',
    }
  }
  if (evidence?.status === 'Not required' || recorded?.required === 'not_required')
    return { label: `No ${name.toLowerCase()}`, tone: 'clear' }
  return { label: `${name}: ask payer`, tone: 'unknown' }
}
