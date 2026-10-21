import type { PrevisitEstimate } from '../../services/api/commercial-previsit'

export function visitServiceLabel(result: PrevisitEstimate | null | undefined): string | undefined {
  if (result?.price_range?.mode !== 'alternatives') {
    return result?.service_plan?.recommendation_basis
  }

  const codes = result.codes.map((row) => row.code)
  const family =
    codes.length && codes.every((code) => /^9920[2-5]$/.test(code))
      ? 'new'
      : codes.length && codes.every((code) => /^9921[2-5]$/.test(code))
        ? 'established'
        : null
  const visitMatchesCodes = result.visit_type === family
  const label = visitMatchesCodes
    ? `${family === 'new' ? 'New' : 'Established'} patient office consultation`
    : 'Office E/M consultation'
  const mismatch =
    result.visit_type && family && !visitMatchesCodes
      ? ' · eCW visit type conflicts with the CPT alternatives'
      : ''
  return `${label} · E/M alternatives ${codes.join(', ')}${mismatch}`
}
