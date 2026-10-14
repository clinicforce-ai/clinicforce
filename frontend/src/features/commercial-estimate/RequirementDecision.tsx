import type { RequirementEvidence } from '../../services/api/commercial-estimator'
import RequirementSourceLink from './RequirementSourceLink'

export function RequirementDecision({
  evidence,
  readOnly = false,
}: {
  evidence?: RequirementEvidence
  readOnly?: boolean
}) {
  const label =
    evidence?.status === 'Not required'
      ? 'No'
      : evidence?.status === 'Required'
        ? 'Yes'
        : 'Rule not stated'
  return (
    <>
      {label}
      {evidence?.approval_status === 'Not verified' && <small>Approval not recorded</small>}
      {evidence?.approval_status === 'Verified' && <small>Approval recorded</small>}
      {evidence && (
        <details className="service-requirement-detail" open={readOnly || undefined}>
          <summary>Evidence</summary>
          <p>{evidence.reason}</p>
          {evidence.approval_reference && <p>Approval reference: {evidence.approval_reference}</p>}
          {evidence.sources.map((source, index) => (
            <RequirementSourceLink key={index} source={source} />
          ))}
        </details>
      )}
    </>
  )
}
