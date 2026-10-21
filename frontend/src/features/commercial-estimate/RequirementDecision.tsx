import type { RequirementEvidence } from '../../services/api/commercial-estimator'
import RequirementSourceLink from './RequirementSourceLink'

export function RequirementDecision({
  evidence,
  readOnly = false,
  code,
  kind,
  payerName,
  needsCodeSelection = false,
  lineRequirement,
}: {
  evidence?: RequirementEvidence
  readOnly?: boolean
  code: string
  kind: 'referral' | 'prior authorization'
  payerName?: string | null
  needsCodeSelection?: boolean
  lineRequirement?: {
    required: string
    status: string
    cleared: boolean
    reference?: string
    verified_at?: string
  }
}) {
  const lineRequired = lineRequirement?.required === 'required'
  const conflict = evidence?.status === 'Not required' && lineRequired
  const status = evidence?.status || (lineRequired ? 'Required' : undefined)
  const label = conflict
    ? `CPT rule conflicts. Ask ${payerName || 'the payer'} about CPT ${code}`
    : status === 'Not required'
      ? needsCodeSelection
        ? 'No if selected'
        : 'No'
      : status === 'Required'
        ? needsCodeSelection
          ? 'Yes if selected'
          : 'Yes'
        : needsCodeSelection
          ? 'Select CPT level'
          : `Ask ${payerName || 'the payer'} whether CPT ${code} needs ${kind}`
  return (
    <>
      {label}
      {kind === 'referral' && evidence && (
        <small>Referral obtained: {evidence.referral_obtained || 'UNKNOWN'}</small>
      )}
      {kind === 'prior authorization' && evidence && (
        <>
          {needsCodeSelection && status === 'Required' && (
            <small>Approval needed if selected</small>
          )}
          <small>PA approval: {evidence.pa_approval || 'UNVERIFIED'}</small>
        </>
      )}
      {!evidence &&
        lineRequired &&
        lineRequirement?.status === 'valid' &&
        lineRequirement.cleared &&
        lineRequirement.reference &&
        lineRequirement.verified_at && (
          <small>CPT source reference and verification time recorded</small>
        )}
      {evidence && (
        <details className="service-requirement-detail" open={readOnly || undefined}>
          <summary>Evidence</summary>
          <p>{evidence.reason}</p>
          {evidence.member_specific_check && <p>Needs member-specific check.</p>}
          {evidence.approval_reference && <p>Approval reference: {evidence.approval_reference}</p>}
          {evidence.sources.map((source, index) => (
            <RequirementSourceLink key={index} source={source} />
          ))}
        </details>
      )}
    </>
  )
}
