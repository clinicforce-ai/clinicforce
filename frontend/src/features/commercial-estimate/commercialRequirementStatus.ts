import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { requirementForLine } from './requirementForLine'
import { aetnaReferenceRequirement } from './aetnaReferenceRequirements'
import { patientBenefitsBound } from './identityBinding'

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
  const aetnaChoiceOffice =
    /^aetna$/i.test(result.payer || '') &&
    result.plan_details?.stedi?.names?.length === 1 &&
    /^aetna choice pos ii$/i.test(result.plan_details.stedi.names[0].trim()) &&
    result.benefits?.active === true &&
    !result.benefits.coverage_dates_invalid &&
    result.benefits.network_status === 'in_network' &&
    patientBenefitsBound(result.identity) &&
    result.service_date >= '2026-11-01' &&
    result.service_date <= '2026-12-31' &&
    lines.length === 1 &&
    lines[0].code === '99203'
  const aetnaChoiceEmg =
    /^aetna$/i.test(result.payer || '') &&
    result.plan_details?.stedi?.names?.length === 1 &&
    /^aetna choice pos ii$/i.test(result.plan_details.stedi.names[0].trim()) &&
    result.benefits?.active === true &&
    patientBenefitsBound(result.identity) &&
    result.service_date >= '2026-11-01' &&
    result.service_date <= '2026-12-31' &&
    lines.length === 2 &&
    lines.every((line) => line.units === 1) &&
    new Set(lines.map((line) => line.code)).size === 2 &&
    lines.every((line) => ['95908', '95886'].includes(line.code))
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
  const referralResearch =
    (aetnaOfficeResearch || aetnaChoiceOffice) && referral === 'CPT 99203: not determined'
  const authorizationResearch =
    (aetnaOfficeResearch || aetnaChoiceOffice) && authorization === 'CPT 99203: not determined'
  const emgReferralResearch =
    aetnaChoiceEmg && lines.every((line) => referral.includes(`CPT ${line.code}: not determined`))
  const emgAuthorizationResearch =
    aetnaChoiceEmg &&
    lines.every((line) => authorization.includes(`CPT ${line.code}: not determined`))
  return [
    {
      label: 'Referral',
      value: emgReferralResearch
        ? 'CPT 95908: No; CPT 95886: No'
        : referralResearch
          ? aetnaChoiceOffice
            ? 'CPT 99203: Aetna Choice POS II generally needs no referral; verify member terms'
            : 'CPT 99203: published PPO says no PCP referral; verify member plan'
          : referral,
      detail: cignaCardReferral
        ? 'The card says no specialist referral is required; confirm any separate selected-CPT requirement.'
        : emgReferralResearch
          ? 'NMCT payer plan matrix places Choice POS II in the no-referral Aetna group; Aetna Choice POS II permits direct specialist access.'
          : referralResearch
            ? aetnaChoiceOffice
              ? 'Aetna Choice POS II published specialist-access guidance; self-funded member terms may differ.'
              : 'Aetna Medicare Signature (PPO) H2293-016 2026 Summary of Benefits; the member’s exact plan ID has not been confirmed.'
            : reason('referral'),
    },
    {
      label: 'Prior authorization',
      value: emgAuthorizationResearch
        ? 'CPT 95908: No; CPT 95886: No'
        : authorizationResearch
          ? 'CPT 99203: absent from Aetna precert list; verify member terms'
          : authorization,
      detail: emgAuthorizationResearch
        ? 'Neither CPT appears on Aetna’s participating-provider precertification list updated 1 Nov 2026. Standard office-based service; final coverage follows the member plan.'
        : authorizationResearch
          ? 'Aetna participating-provider precertification list updated 1 Nov 2026; this research is not member-specific approval.'
          : reason('authorization'),
    },
  ]
}
