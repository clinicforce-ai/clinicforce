import { type V2Resource } from '../../services/api/insurance-v2-types'

export const money = (value?: number | null) =>
  value == null
    ? 'Not returned'
    : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value / 100)
export const readable = (value?: string) =>
  value?.replace(/([a-z])([A-Z])/g, '$1 $2').replaceAll('_', ' ') || 'Not returned'
export const dateTime = (value?: string) =>
  value ? new Date(value).toLocaleString() : 'Not returned'
export const resources: Array<{ id: V2Resource; label: string; action: string; help: string }> = [
  {
    id: 'coverage',
    label: 'Eligibility and benefits',
    action: 'Check eligibility',
    help: 'Confirm active coverage and return payer benefit details.',
  },
  {
    id: 'referral',
    label: 'Referral',
    action: 'Submit referral request',
    help: 'Use when staff intend to initiate a referral review.',
  },
  {
    id: 'authorization',
    label: 'Prior authorization',
    action: 'Submit authorization request',
    help: 'Use when staff intend to initiate a prior authorization review.',
  },
  {
    id: 'estimate',
    label: 'Scheduled visit estimate',
    action: 'Get scheduled visit estimate',
    help: 'Estimate only the services marked Scheduled.',
  },
  {
    id: 'procedure_estimate',
    label: 'Possible procedure estimate',
    action: 'Get possible procedure estimate',
    help: 'Keep a possible follow-up procedure separate from today’s visit.',
  },
]
