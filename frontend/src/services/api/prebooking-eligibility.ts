import { json, request } from './client'

export type PrebookingDetails = {
  payer_id: string
  given_name: string
  family_name: string
  birth_date: string
  member_id: string
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

export const getPrebookingOptions = (query: string) =>
  request<{
    payers: Array<{ id: string; name: string }>
  }>(`/api/staff/prebooking-eligibility/options?query=${encodeURIComponent(query)}`)

export const checkPrebookingEligibility = (details: PrebookingDetails) =>
  request<PrebookingResult>('/api/staff/prebooking-eligibility/check', json(details))
