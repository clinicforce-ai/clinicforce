import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { money } from './format'

export default function VisitPatientTotals({
  result,
  officeOnly,
}: {
  result: PrevisitEstimate
  officeOnly: boolean
}) {
  const summary = result.encounter_summary
  const amountCents = result.collection_guidance?.amount_cents
  if (amountCents == null) return null
  const visitTotal =
    !officeOnly &&
    summary?.complete === true &&
    summary.conditional !== true &&
    summary.estimated_total_patient_min_cents != null &&
    summary.estimated_total_patient_min_cents === summary.estimated_total_patient_max_cents &&
    summary.estimated_total_patient_min_cents >= amountCents
      ? summary.estimated_total_patient_min_cents
      : null
  return (
    <dl className="arrival-money-picture" aria-label="Collection and visit patient totals">
      <div>
        <dt>{officeOnly ? 'Office advance to collect now' : 'Total to collect now'}</dt>
        <dd>{money(amountCents)}</dd>
      </div>
      {visitTotal != null && (
        <div>
          <dt>Estimated patient cost for this visit</dt>
          <dd>{money(visitTotal)}</dd>
        </div>
      )}
      {visitTotal != null && (summary?.estimated_later_balance_cents ?? 0) > 0 && (
        <div>
          <dt>Estimated balance after today</dt>
          <dd>{money(summary!.estimated_later_balance_cents!)}</dd>
        </div>
      )}
    </dl>
  )
}
