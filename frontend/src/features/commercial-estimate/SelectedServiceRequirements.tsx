import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { requirementForLine } from './requirementForLine'
import RequirementSourceLink from './RequirementSourceLink'

export default function SelectedServiceRequirements({
  result,
  payerName,
}: {
  result: PrevisitEstimate
  payerName: string
}) {
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
          const referral = referralRequired
            ? referralLine?.status === 'valid' &&
              referralLine.cleared &&
              referralLine.reference &&
              referralLine.verified_at
              ? 'referral on file'
              : 'referral needed'
            : decision?.referral?.status === 'Not required'
              ? 'no referral'
              : `ask ${payerName} about referral`
          const authorization = authorizationRequired
            ? authorizationLine?.status === 'valid' &&
              authorizationLine.cleared &&
              authorizationLine.reference &&
              authorizationLine.verified_at
              ? decision?.authorization?.approval_status === 'Verified' ||
                (!decision &&
                  result.requirement_evidence?.authorization?.approval_status === 'Verified')
                ? 'prior authorization approved'
                : 'prior authorization on file'
              : 'prior authorization needed'
            : decision?.authorization?.status === 'Not required'
              ? 'no prior authorization'
              : `ask ${payerName} about prior authorization`
          return (
            <li key={line.id}>
              <strong>CPT {line.code}</strong>
              {Number(line.units) > 1 ? ` × ${line.units}` : ''}: {referral}, {authorization}.
              {(!!decision?.referral?.sources?.length ||
                !!decision?.authorization?.sources?.length) && (
                <details>
                  <summary>Rule sources</summary>
                  {!!decision?.referral?.sources?.length && (
                    <small>
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
                      Authorization:{' '}
                      {decision.authorization.sources.map((source, index) => (
                        <span key={index}>
                          <RequirementSourceLink source={source} />{' '}
                        </span>
                      ))}
                    </small>
                  )}
                </details>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
