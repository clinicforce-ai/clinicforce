import { json, request } from './client'

export type PrebookingDetails = {
  payer_connection_id: string
  provider_network_id: string
  given_name: string
  family_name: string
  birth_date: string
  member_id: string
  relationship: 'self' | 'dependent'
  subscriber_given_name: string
  subscriber_family_name: string
  subscriber_birth_date: string
}

export type PrebookingResult = {
  status: 'active_today' | 'inactive_today' | 'needs_review'
  message: string
  checked_at: string
  checked_on: string
  payer: string
  check_id: string | null
  coverage_start: string | null
  coverage_end: string | null
}

export const getPrebookingOptions = () =>
  request<{
    payers: Array<{
      id: string
      name: string
      providers: Array<{ id: string; name: string; npi: string }>
    }>
  }>('/api/staff/prebooking-eligibility/options')

export const checkPrebookingEligibility = (details: PrebookingDetails) =>
  request<PrebookingResult>(
    '/api/staff/prebooking-eligibility/check',
    json({
      ...details,
      subscriber_birth_date:
        details.relationship === 'dependent' ? details.subscriber_birth_date : null,
    }),
  )
