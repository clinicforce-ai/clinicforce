export function serviceRowLabel({
  description,
  reason,
  code,
  units,
}: {
  description?: string | null
  reason?: string | null
  code: string
  units?: number | string | null
}) {
  const service = description?.trim() || reason?.trim() || 'Planned service'
  const codeType = /^\d{5}$/.test(code) ? 'CPT' : 'HCPCS'
  return `${service} · ${codeType} ${code} × ${units || 1}`
}
