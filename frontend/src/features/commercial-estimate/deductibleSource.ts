import type { PrevisitBenefits } from '../../services/api/commercial-benefits'
import type { VisitPricedLine } from '../../services/api/commercial-visit-types'

export function selectedDeductibleValue(lines: VisitPricedLine[]): string {
  return lines.length && lines.every((line) => line.benefits?.deductible_applies === false)
    ? 'Does not apply to selected CPTs'
    : 'Not returned for this visit'
}

export function hasGeneralOnlyOfficeDeductible(benefits: PrevisitBenefits): boolean {
  const options = benefits?.reported_options || []
  return (
    options.some(
      (row) =>
        row.kind === 'deductible' &&
        row.service_type_code === '30' &&
        row.coverage_level === 'INDIVIDUAL' &&
        row.network === 'IN_NETWORK' &&
        row.time_period === 'REMAINING' &&
        row.amount_cents === benefits?.deductible_remaining_cents,
    ) &&
    !options.some(
      (row) =>
        row.kind === 'deductible' && row.service_type_code === '98' && row.network === 'IN_NETWORK',
    )
  )
}
