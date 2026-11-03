import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
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

export function commercialRequirementRows(
  result: PrevisitEstimate,
  aetnaRateReference: boolean,
  cignaCardReferral: boolean,
): CostBreakdownRow[] {
  const lines = result.price_range?.mode === 'services' ? result.price_range.lines || [] : []
  const aetnaOfficeResearch =
    aetnaRateReference &&
    result.service_date >= '2026-11-01' &&
    result.service_date <= '2026-12-31' &&
    lines.length === 1 &&
    lines[0].code === '99203'
  const referral = requirementStatus(result, 'referral')
  const authorization = requirementStatus(result, 'authorization')
  const reason = (kind: 'referral' | 'authorization') =>
    [
      ...new Set(
        lines
          .map(
            (line) =>
              requirementForLine(result.requirement_evidence?.procedures, line.id, line.code)?.[
                kind
              ]?.reason,
          )
          .filter(Boolean),
      ),
    ].join(' ') || undefined
  const referralResearch = aetnaOfficeResearch && referral === 'CPT 99203: not determined'
  const authorizationResearch = aetnaOfficeResearch && authorization === 'CPT 99203: not determined'
  return [
    {
      label: 'Referral',
      value: referralResearch
        ? 'CPT 99203: published PPO says no PCP referral; verify member plan'
        : referral,
      detail: cignaCardReferral
        ? 'The card says no specialist referral is required; confirm any separate selected-CPT requirement.'
        : referralResearch
          ? 'Aetna Medicare Signature (PPO) H2293-016 2026 Summary of Benefits; the member’s exact plan ID has not been confirmed.'
          : reason('referral'),
    },
    {
      label: 'Prior authorization',
      value: authorizationResearch
        ? 'CPT 99203: absent from Aetna precert list; verify member terms'
        : authorization,
      detail: authorizationResearch
        ? 'Aetna participating-provider precertification list updated 1 Nov 2026; this research is not member-specific approval.'
        : reason('authorization'),
    },
  ]
}
