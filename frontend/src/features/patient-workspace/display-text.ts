export function dateLabel(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        ...(/^\d{4}-\d{2}-\d{2}$/.test(value) ? { timeZone: 'UTC' } : {}),
      })
}

export function patientInitials(patientId: string): string {
  return patientId.slice(0, 2).toUpperCase() || 'PT'
}

export function codedText(value: unknown): string | null {
  if (!value || typeof value !== 'object') return null
  const candidate = value as { text?: unknown; coding?: unknown }
  if (typeof candidate.text === 'string' && candidate.text.trim()) return candidate.text.trim()
  if (Array.isArray(candidate.coding)) {
    for (const coding of candidate.coding) {
      if (!coding || typeof coding !== 'object') continue
      const display =
        (coding as { display?: unknown; code?: unknown }).display ??
        (coding as { code?: unknown }).code
      if (typeof display === 'string' && display.trim()) return display.trim()
    }
  }
  return null
}
