export const money = (cents: number) =>
  new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(cents / 100)
export const range = (min: number, max: number) =>
  min === max ? money(min) : `${money(min)} – ${money(max)}`
export const memberSource = (value?: string | null) =>
  value === 'card_ocr'
    ? 'Insurance card OCR · confirm against the image'
    : value?.startsWith('Coverage.')
      ? `eCW ${value}`
      : value || 'Source not retained'
const dateLabel = (value: string, options: Intl.DateTimeFormatOptions) => {
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value)
  return Number.isNaN(date.getTime()) ? 'Date unavailable' : date.toLocaleString(undefined, options)
}
export const visitDate = (value: string) =>
  dateLabel(value, { month: 'short', day: 'numeric', year: 'numeric' })
export const visitTime = (value: string, timeZone?: string) =>
  dateLabel(value, {
    hour: 'numeric',
    minute: '2-digit',
    ...(timeZone ? { timeZone, timeZoneName: 'short' } : {}),
  })
