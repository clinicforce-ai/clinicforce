import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { VisitPricedLine } from '../../services/api/commercial-visit-types'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { aetnaSignatureOfficeNoDeductible } from './payerReportedPartialCopay'
import { money, range } from './format'

export function commercialCashAlternativeRow(
  price: PrevisitEstimate['price_range'],
  cashPrice: string | null,
  automaticCashFallback: boolean,
  pricedCashLines: VisitPricedLine[],
): CostBreakdownRow {
  return {
    label: 'Cash alternative',
    value:
      automaticCashFallback && cashPrice
        ? cashPrice
        : price?.cash_complete && price.cash_min_cents != null && price.cash_max_cents != null
          ? range(price.cash_min_cents, price.cash_max_cents)
          : pricedCashLines.length === 1 && /^9920[2-5]$/.test(pricedCashLines[0].code)
            ? `${money(pricedCashLines[0].cash_cents!)} new-patient office visit`
            : 'Not quoted',
    detail: 'Separate self-pay price; not used as the insurance allowed amount',
  }
}

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
        result.service_selection_status === 'preview' &&
        result.service_plan?.method === 'llm' &&
        !aetnaSignatureOfficeNoDeductible(result, lines.length === 1 ? lines[0] : null)
          ? 'LLM-proposed CPTs'
          : 'CPTs',
      value: lines.length
        ? lines
            .map(
              (line) =>
                `CPT ${line.code} × ${line.units || 1}${line.modifiers?.length ? ` · modifier ${line.modifiers.join(', ')}` : ''}`,
            )
            .join('; ')
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
