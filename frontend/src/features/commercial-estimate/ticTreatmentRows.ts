import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'

export function ticTreatmentRows(result: PrevisitEstimate): CostBreakdownRow[] {
  if (!/\btics?\b/i.test(result.reason || '')) return []
  return [
    {
      label: 'Suggested option · CBIT',
      value: 'Consider for impairing tics; CPT 90834 alone or add-on 90836 with a distinct E/M',
      detail:
        'A qualified clinician must plan or perform and document 45-minute individual psychotherapy. This option is not included in the visit estimate.',
    },
    {
      label: 'Suggested option · focal botulinum toxin',
      value:
        'Consider for an older adolescent or adult with a localized, bothersome tic; injection CPT depends on muscle and site',
      detail:
        'CPT 64612 is an example for unilateral facial nerve muscles. J0585 applies only to onabotulinumtoxinA with documented units. Neither injection nor drug is included; check coverage and authorization if selected.',
    },
  ]
}
