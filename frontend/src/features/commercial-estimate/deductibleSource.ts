import type { PrevisitBenefits } from '../../services/api/commercial-benefits'

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
