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

export type PrebookingPlan = {
  groups: Array<{
    label: string
    selection_mode: 'single' | 'multiple'
    codes: Array<{ code: string; reason: string; role: string; service_type: string | null }>
  }>
  notes: string[]
  message: string
}

export type PrebookingEstimateDetails = PrebookingDetails & {
  reason: string
  visit_type: 'new' | 'established' | 'unknown'
  service_date: string | null
  network_context_id: string | null
  relationship: 'self' | 'dependent'
  subscriber_given_name: string
  subscriber_family_name: string
  subscriber_birth_date: string | null
  selected_services: Array<{ code: string; units: number }>
}

export type PrebookingTerms = {
  copay_cents: number | null
  coinsurance_percent: string | null
  deductible_annual_cents: number | null
  deductible_remaining_cents: number | null
  oop_annual_cents: number | null
  oop_remaining_cents: number | null
  deductible_applicability: string | null
  authorization_signal: string | null
  referral_signal: string | null
  resolved_service_type: string | null
}

export type PrebookingEstimate = {
  coverage: Pick<PrebookingResult, 'status' | 'message' | 'coverage_start' | 'coverage_end'>
  payer: string
  checked_at: string
  checked_on: string
  check_ids: string[]
  patient_address: {
    line1: string | null
    line2: string | null
    city: string | null
    state: string | null
    postal_code: string | null
    country: string | null
  } | null
  network_status: 'in_network' | 'out_of_network' | 'unknown'
  network_evidence: { reason?: string; decision?: string; source?: string } | null
  plan_name: string | null
  benefits: PrebookingTerms | null
  lines: Array<{
    code: string
    units: number
    role: string
    service_type: string | null
    allowed_cents: number | null
    patient_cents: number | null
    rate_source: string | null
    benefits: PrebookingTerms
    issues: string[]
    conditions: string[]
  }>
  allowed_total_cents: number | null
  plan_total_cents: number | null
  patient_min_cents: number | null
  patient_max_cents: number | null
  estimate_status: 'priced' | 'conditional' | 'needs_review'
  issues: string[]
}

export const getPrebookingContexts = () =>
  request<{ contexts: Array<{ id: string; label: string }> }>(
    '/api/staff/prebooking-estimate/contexts',
  )

export const planPrebookingServices = (
  reason: string,
  visit_type: PrebookingEstimateDetails['visit_type'],
) => request<PrebookingPlan>('/api/staff/prebooking-estimate/plan', json({ reason, visit_type }))

export const checkPrebookingEstimate = (details: PrebookingEstimateDetails) =>
  request<PrebookingEstimate>('/api/staff/prebooking-estimate/check', json(details))
