import type { VisitPricedLine } from '../../services/api/commercial-visit-types'

export function resolvedAllowedTotal(lines: VisitPricedLine[]): number | null {
  if (
    !lines.length ||
    !lines.every(
      (line) =>
        line.rate_resolution?.status === 'RESOLVED' &&
        line.allowed_cents != null &&
        ['clinic_contract', 'payer_out_of_network', 'cms_original_medicare_estimate'].includes(
          line.allowed_basis || '',
        ),
    )
  )
    return null
  return lines.reduce((sum, line) => sum + line.allowed_cents!, 0)
}
