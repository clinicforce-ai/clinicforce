import { json, request } from './client'

export type V2Resource = 'coverage' | 'estimate' | 'procedure_estimate' | 'authorization' | 'referral'
export type V2Document = 'card' | 'provider' | 'appointment' | 'order' | 'charges' | 'fees'
export interface V2Configuration {
  environment: 'demo' | 'production'; credentials_configured: boolean; practice_configured: boolean
  ecw_configured: boolean; production_enabled: boolean; message: string
}
export interface V2Benefit {
  category: string; network: string; service?: string; level?: string; description?: string
  amount?: string; remaining?: string; total?: string
  amountTimePeriod?: string; remainingTimePeriod?: string; totalTimePeriod?: string
  authorizationRequired?: boolean; authorizationRequiredUnknown?: boolean; referralNumber?: string
}
export interface V2Plan {
  name?: string; planName?: string; planNumber?: string; groupNumber?: string; status?: string
  insuranceType?: string; effectiveDate?: string; expirationDate?: string; benefits: V2Benefit[]; notes: string[]
}
export interface V2PriceLine {
  code?: string; description?: string; quantity?: string; billed_cents?: number | null
  patient_cents?: number | null; allowed_cents?: number | null; copay_cents?: number | null
  coinsurance_cents?: number | null; deductible_cents?: number | null; not_covered_cents?: number | null; notes: string[]
}
export interface V2ReviewLine {
  code?: string; status?: string; statusCode?: string; certificationNumber?: string
  certificationEffectiveDate?: string; certificationExpirationDate?: string; quantity?: string; quantityType?: string
  fromDate?: string; toDate?: string; notes: string[]
}
export interface V2Transaction {
  outcome: 'submitting' | 'pending' | 'complete' | 'input_error' | 'payer_error' | 'unavailable' | 'indeterminate'
  message?: string; resource_id?: string; transaction_id?: string; submitted_at?: string; checked_at?: string
  mock_response?: boolean; next_poll_at?: number
  http_status?: number
  result?: {
    payer?: string; status?: string; active?: boolean | null; as_of_date?: string; plans?: V2Plan[]; pce_available?: boolean
    provider_network?: string; notes?: string[]; message?: string; disclaimer?: string
    estimate_available?: boolean; lines?: V2PriceLine[]; patient_cents?: number | null; allowed_cents?: number | null
    status_code?: string; certificationNumber?: string; certificationEffectiveDate?: string; certificationExpirationDate?: string
    fromDate?: string; toDate?: string; procedures?: V2ReviewLine[]
  }
}
export interface V2Case {
  id: string; revision: number; created_at: string; expires_at: string | null; environment: 'demo' | 'production'
  sources: Partial<Record<V2Document | 'ecw', {
    filename?: string; sha256?: string; observed_at?: string; retrieved_at?: string; system?: string
    appointment_date?: string; appointment_status?: string; payer?: string; location?: string
    visit_type?: 'new' | 'established' | 'not_returned'; history_status?: 'loaded' | 'timed_out' | 'unavailable'
    recent_completed_appointments?: Array<{ date: string; status: string; visit_type: string; reason?: string | null; coded_services: string[] }>
    billing_provider?: { name?: string; npi?: string }; rendering_provider?: { name?: string; npi?: string; roles?: string[] }
    missing?: string[]; timed_out_resources?: string[]; unavailable_resources?: string[]; preserved_resources?: string[]
    resources?: Record<string, number>
    rate_count?: number; notes?: string[]
  }>>
  missing: string[]
  missing_by_resource: Record<V2Resource, string[]>
  inputs: {
    patient: { patient_first_name?: string; patient_last_name?: string; patient_birth_date?: string }
    subscriber?: { firstName?: string; lastName?: string; birthDate?: string; memberId?: string }
    provider: { npi?: string; lastName?: string; addressLine1?: string; city?: string; stateCode?: string; zipCode?: string }
    member_id?: string; payer_id?: string; group_number?: string; relationship?: string; patient_phone?: string
    service_date?: string; reason?: string; visit_type?: 'new' | 'established' | null
    diagnoses: string[]; coverage_missing: string[]; review_missing: string[]
    lines: Array<{ code: string; description?: string; units?: string; charge?: string; cash_cents?: number | null; service_status?: 'scheduled' | 'possible'; modifiers: string[] }>
  }
  transactions: Partial<Record<V2Resource, V2Transaction>>
  discovery: {
    checked_at?: string; message?: string; outcome?: string
    requirements?: Partial<Record<V2Resource, { available: boolean; message?: string; configuration?: { elements?: Record<string, { label?: string; required?: boolean }> } }>>
  }
  suggestions?: {
    groups: Array<{ id: string; label: string; category: string; selection_mode: 'single' | 'multiple'; description?: string; requires_groups: string[]; codes: string[] }>
    lines: Array<{ code: string; description?: string; charge?: string; cash_cents?: number | null; group_id: string; recommendation_reason: string; requires_quantity: boolean; unit_label: string }>
    notes: string[]
    requires_fee_schedule?: boolean
  }
  summary: {
    action: 'collect' | 'verify' | 'do_not_schedule' | 'self_pay'; heading: string
    coverage: 'active' | 'inactive' | 'unconfirmed'; hmo: boolean
    referral: { required: boolean; unknown: boolean; approved: boolean; status?: string; reference?: string; effective?: string; expiration?: string }
    authorization: { required: boolean; unknown: boolean; approved: boolean; status?: string; reference?: string; effective?: string; expiration?: string }
    scheduled: { codes: string[]; insurance_cents?: number | null; allowed_cents?: number | null; cash_cents?: number | null; network?: string }
    possible: { codes: string[]; insurance_cents?: number | null; allowed_cents?: number | null; cash_cents?: number | null; network?: string }
  }
  events: Array<{ stage: string; at: string }>
}
const base = '/api/doctor/insurance/v2'
export const getV2Configuration = () => request<V2Configuration>(`${base}/configuration`)
export const latestV2Case = () => request<{ case: V2Case | null }>(`${base}/cases/latest`)
export const createV2Case = (scenario = 'Coverages-Complete-i') => request<V2Case>(`${base}/cases`, json({ scenario }))
export const getV2Case = (id: string) => request<V2Case>(`${base}/cases/${encodeURIComponent(id)}`)
export const getV2Requirements = (id: string) => request<V2Case>(`${base}/cases/${encodeURIComponent(id)}/requirements`, json({}))
export const suggestV2Services = (id: string) => request<V2Case>(`${base}/cases/${encodeURIComponent(id)}/suggestions`, json({}))
export const confirmV2Services = (id: string, lines: Array<{ code: string; quantity: number; service_status: 'scheduled' | 'possible' }>) => request<V2Case>(`${base}/cases/${encodeURIComponent(id)}/services`, json({ lines }))
export const submitV2Transaction = (id: string, resource: V2Resource) => request<V2Case>(`${base}/cases/${encodeURIComponent(id)}/transactions/${resource}`, json({}))
export const refreshV2Transaction = (id: string, resource: V2Resource) => request<V2Case>(`${base}/cases/${encodeURIComponent(id)}/transactions/${resource}/refresh`, json({}))
export const retryV2Transaction = (id: string, resource: V2Resource) => request<V2Case>(`${base}/cases/${encodeURIComponent(id)}/transactions/${resource}/retry`, json({}))
export function uploadV2Document(id: string, kind: V2Document, file: File) {
  const body = new FormData()
  body.append('file', file)
  return request<V2Case>(`${base}/cases/${encodeURIComponent(id)}/documents/${kind}`, { method: 'POST', body })
}
