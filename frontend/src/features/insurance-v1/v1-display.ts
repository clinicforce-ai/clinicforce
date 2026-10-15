import { type InsuranceStage } from '../../services/api/insurance'
export const steps: Array<{ id: InsuranceStage; label: string }> = [
  { id: 'intake', label: 'Insurance card' },
  { id: 'eligibility', label: 'Card review' },
  { id: 'network', label: 'Benefits & rates' },
  { id: 'service', label: 'Select CPT codes' },
  { id: 'authorization', label: 'Authorization & referral' },
  { id: 'estimate', label: 'Pay estimate' },
]
export const imageOrPdf = '.pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png'
export const money = (cents: number | null | undefined) =>
  cents == null
    ? 'Not available'
    : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(cents / 100)
export const label = (key: string) =>
  key.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
export const authorizationLabel = (status: string | undefined) =>
  status === 'not_required'
    ? 'Not required'
    : status === 'required'
      ? 'Required'
      : status === 'conditional'
        ? 'Conditional · review source terms'
        : 'Not established by the uploaded documents'
export const timestamp = (value: string) =>
  Number.isNaN(Date.parse(value))
    ? value
    : new Date(value).toLocaleString(undefined, {
        dateStyle: 'medium',
        timeStyle: 'short',
        timeZoneName: undefined,
      })
