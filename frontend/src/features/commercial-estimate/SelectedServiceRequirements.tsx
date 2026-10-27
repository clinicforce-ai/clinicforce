import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { requirementForLine } from './requirementForLine'
import RequirementSourceLink from './RequirementSourceLink'

export default function SelectedServiceRequirements({ result }: { result: PrevisitEstimate }) {
  const lines = (result.price_range?.lines || []).filter((line) => line.code)
  if (!lines.length || result.price_range?.mode !== 'services') return null
  return (
    <div className="selected-cpt-requirements">
      <small>Planned CPTs · referral and prior authorization</small>
      <ul>
        {lines.map((line) => {
          const decision = requirementForLine(
            result.requirement_evidence?.procedures,
            line.id,
            line.code,
          )
          const referralLine = line.benefits?.requirements?.referral
          const authorizationLine = line.benefits?.requirements?.authorization
          const referralRequired =
            decision?.referral?.status === 'Required' || referralLine?.required === 'required'
          const authorizationRequired =
            decision?.authorization?.status === 'Required' ||
            authorizationLine?.required === 'required'
          const referralConflict =
            (decision?.referral?.status === 'Required' &&
              referralLine?.required === 'not_required') ||
            (decision?.referral?.status === 'Not required' && referralLine?.required === 'required')
          const authorizationConflict =
            (decision?.authorization?.status === 'Required' &&
              authorizationLine?.required === 'not_required') ||
            (decision?.authorization?.status === 'Not required' &&
              authorizationLine?.required === 'required')
          const referralCleared =
            referralLine?.status === 'valid' &&
            referralLine.cleared &&
            referralLine.reference &&
            referralLine.verified_at
          const authorizationCleared =
            authorizationLine?.status === 'valid' &&
            authorizationLine.cleared &&
            authorizationLine.reference &&
            authorizationLine.verified_at &&
            (decision?.authorization?.approval_status ??
              result.requirement_evidence?.authorization?.approval_status) === 'Verified'
          const authorizationReferenceOnFile =
            authorizationLine?.status === 'valid' &&
            authorizationLine.cleared &&
            authorizationLine.reference &&
            authorizationLine.verified_at &&
            !(
              decision?.authorization?.approval_status ??
              result.requirement_evidence?.authorization?.approval_status
            )
          const referral = referralConflict
            ? 'review referral rule'
            : referralRequired
              ? referralCleared
                ? 'referral on file'
                : 'referral required'
              : decision?.referral?.status === 'Not required'
                ? 'no referral'
                : 'referral not determined'
          const authorization = authorizationConflict
            ? 'review prior authorization rule'
            : authorizationRequired
              ? authorizationCleared
                ? 'prior authorization approved'
                : authorizationReferenceOnFile
                  ? 'prior authorization on file'
                  : 'prior authorization required'
              : decision?.authorization?.status === 'Not required' ||
                  authorizationLine?.required === 'not_required'
                ? 'no prior authorization'
                : 'prior authorization not determined'
          return (
            <li key={line.id}>
              <strong>CPT {line.code}</strong>
              {Number(line.units) > 1 ? ` × ${line.units}` : ''}:{' '}
              <span
                className={`requirement-tag ${referralConflict ? 'is-unknown' : referralRequired ? (referralCleared ? 'is-clear' : 'is-required') : decision?.referral?.status === 'Not required' ? 'is-clear' : 'is-unknown'}`}
              >
                {referral}
              </span>
              ,{' '}
              <span
                className={`requirement-tag ${authorizationConflict ? 'is-unknown' : authorizationRequired ? (authorizationCleared ? 'is-clear' : authorizationReferenceOnFile ? 'is-unknown' : 'is-required') : decision?.authorization?.status === 'Not required' || authorizationLine?.required === 'not_required' ? 'is-clear' : 'is-unknown'}`}
              >
                {authorization}
              </span>
              .
              {!!decision?.referral?.sources?.length && (
                <small>
                  {' '}
                  Referral:{' '}
                  {decision.referral.sources.map((source, index) => (
                    <span key={index}>
                      <RequirementSourceLink source={source} />{' '}
                    </span>
                  ))}
                </small>
              )}
              {!!decision?.authorization?.sources?.length && (
                <small>
                  {' '}
                  Authorization:{' '}
                  {decision.authorization.sources.map((source, index) => (
                    <span key={index}>
                      <RequirementSourceLink source={source} />{' '}
                    </span>
                  ))}
                </small>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
