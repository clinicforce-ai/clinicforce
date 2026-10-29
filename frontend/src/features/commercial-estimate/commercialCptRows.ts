import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { VisitPricedLine } from '../../services/api/commercial-visit-types'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'

export function commercialCptRows(
  result: PrevisitEstimate,
  lines: VisitPricedLine[],
  preview: PrevisitEstimate['botox_preview'],
): { cpt: CostBreakdownRow; candidates: CostBreakdownRow[]; unpriced: CostBreakdownRow } {
  const suggestedCodes = [
    ...new Set(
      (result.suggestions?.groups || [])
        .flatMap((group) => group.codes.map((candidate) => candidate.code))
        .filter(
          (code) =>
            result.available_codes?.some((row) => row.code === code) &&
            (result.service_date < '2014-01-01' || !['64613', '64614'].includes(code)),
        ),
    ),
  ]
  return {
    cpt: {
      label:
        result.service_selection_status === 'preview' && result.service_plan?.method === 'llm'
          ? 'LLM-proposed CPTs'
          : 'CPTs',
      value: lines.length
        ? lines
            .map(
              (line) =>
                `CPT ${line.code} × ${line.units || 1}${line.modifiers?.length ? ` · modifier ${line.modifiers.join(', ')}` : ''}`,
            )
            .join('; ')
        : preview
          ? `CPT ${preview.code} × 1 · provisional coding scenario`
          : 'No CPTs selected',
      detail:
        result.service_selection_status !== 'approved'
          ? result.service_plan?.method === 'llm'
            ? 'Model proposal used for this estimate; billed codes follow the services actually performed'
            : 'Provisional service scenario used for this estimate'
          : undefined,
    },
    candidates:
      !lines.length && suggestedCodes.length
        ? [
            {
              label: 'Coding assistant candidates',
              value: suggestedCodes.map((code) => `CPT ${code}`).join('; '),
              detail: 'Confirm the planned injection anatomy before selecting a billable CPT.',
            },
          ]
        : [],
    unpriced: {
      label: 'Planned CPTs',
      value: preview ? `CPT ${preview.code} × 1 · provisional` : 'No priced CPTs selected',
      detail: preview
        ? 'Procedure-only scenario; Botox drug and separate E/M are excluded'
        : undefined,
    },
  }
}
