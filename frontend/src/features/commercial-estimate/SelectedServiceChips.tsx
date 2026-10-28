import { Check } from 'lucide-react'
import type { PrevisitEstimate, VisitCode } from '../../services/api/commercial-estimator'
import { requirementTag } from './plannerRequirementTag'

export function SelectedServiceChips({
  result,
  chosen,
  quantities,
}: {
  result: PrevisitEstimate
  chosen: VisitCode[]
  quantities: Record<string, number>
}) {
  return (
    <div className="selected-service-chips" aria-label="Selected services">
      {chosen.map((row) => {
        const referral = requirementTag(result, row, 'referral')
        const authorization = requirementTag(result, row, 'authorization')
        return (
          <span key={row.id}>
            <Check size={14} aria-hidden="true" />
            <strong>
              {row.code} × {quantities[row.id!] || '—'}
            </strong>
            {result.payment_mode !== 'cash' && (
              <>
                <small className={`service-requirement-tag is-${referral.tone}`}>
                  {referral.label}
                </small>
                <small className={`service-requirement-tag is-${authorization.tone}`}>
                  {authorization.label}
                </small>
              </>
            )}
          </span>
        )
      })}
      {!chosen.length && (
        <span>
          {result.provisional_scenario?.kind === 'ncs_study_count'
            ? 'One NCS scenario · study count pending · CPT levels are alternatives'
            : result.price_range?.mode === 'alternatives'
              ? 'One office consultation · E/M level pending'
              : 'No services selected'}
        </span>
      )}
    </div>
  )
}
