import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { requirementForLine } from './requirementForLine'
import { aetnaReferenceRequirement } from './aetnaReferenceRequirements'

export function requirementStatus(result: PrevisitEstimate, kind: 'referral' | 'authorization') {
  const lines = result.price_range?.mode === 'services' ? result.price_range.lines || [] : []
  if (!lines.length) {
    const code = result.botox_preview?.code
    const candidate = result.candidate_requirements?.find((row) => row.code === code)
    return candidate && code
      ? `CPT ${code} candidate: ${candidate[kind]?.status?.toLowerCase() || 'not determined'}`
      : 'Select planned CPTs to check'
  }
  return lines
    .map((line) => {
      const evidence = requirementForLine(
        result.requirement_evidence?.procedures,
        line.id,
        line.code,
      )?.[kind]
      const lineRule = line.benefits?.requirements?.[kind]
      const conflict =
        (evidence?.status === 'Required' && lineRule?.required === 'not_required') ||
        (evidence?.status === 'Not required' && lineRule?.required === 'required')
      const clearanceOnFile =
        lineRule?.status === 'valid' &&
        lineRule.cleared &&
        lineRule.reference &&
        lineRule.verified_at
      const status =
        aetnaReferenceRequirement(result, kind, line.code) ??
        (conflict
          ? 'conflicting rules'
          : evidence?.status === 'Not required' || lineRule?.required === 'not_required'
            ? 'not required'
            : evidence?.status === 'Required' || lineRule?.required === 'required'
              ? kind === 'authorization'
                ? clearanceOnFile && evidence?.approval_status === 'Verified'
                  ? 'approval verified'
                  : 'required; approval status unresolved'
                : clearanceOnFile
                  ? 'referral on file'
                  : 'required; referral status unresolved'
              : 'not determined')
      return `CPT ${line.code}: ${status}`
    })
    .join('; ')
}
