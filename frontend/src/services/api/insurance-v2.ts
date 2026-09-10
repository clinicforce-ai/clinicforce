import { json, request } from './client'

export type V2Resource = 'coverage' | 'estimate' | 'authorization' | 'referral'
export type V2Document = 'card' | 'provider' | 'appointment' | 'order' | 'charges'
export interface V2Configuration {
  environment: 'demo' | 'production'; credentials_configured: boolean; practice_configured: boolean
  production_enabled: boolean; message: string
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
    payer?: string; status?: string; as_of_date?: string; plans?: V2Plan[]; pce_available?: boolean
    provider_network?: string; notes?: string[]; message?: string; disclaimer?: string
    estimate_available?: boolean; lines?: V2PriceLine[]; patient_cents?: number | null; allowed_cents?: number | null
    status_code?: string; certificationNumber?: string; certificationEffectiveDate?: string; certificationExpirationDate?: string
    fromDate?: string; toDate?: string; procedures?: V2ReviewLine[]
  }
}
export interface V2Case {
  id: string; revision: number; created_at: string; expires_at: string; environment: 'demo' | 'production'
  sources: Partial<Record<V2Document, { filename: string; sha256: string; observed_at: string; notes: string[] }>>
  missing: string[]
  inputs: {
    patient: { patient_first_name?: string; patient_last_name?: string; patient_birth_date?: string }
    provider: { npi?: string; lastName?: string; addressLine1?: string; city?: string; stateCode?: string; zipCode?: string }
    member_id?: string; payer_id?: string; relationship?: string; service_date?: string; reason?: string
    diagnoses: string[]; coverage_missing: string[]; review_missing: string[]
    lines: Array<{ code: string; units?: string; charge?: string; modifiers: string[] }>
  }
  transactions: Partial<Record<V2Resource, V2Transaction>>
  discovery: {
    checked_at?: string; message?: string; outcome?: string
    requirements?: Partial<Record<V2Resource, { available: boolean; message?: string; configuration?: { elements?: Record<string, { label?: string; required?: boolean }> } }>>
  }
  events: Array<{ stage: string; at: string }>
}
const base = '/api/doctor/insurance/v2'
export const getV2Configuration = () => request<V2Configuration>(`${base}/configuration`)
export const latestV2Case = () => request<{ case: V2Case | null }>(`${base}/cases/latest`)
export const createV2Case = (scenario = 'Coverages-Complete-i') => request<V2Case>(`${base}/cases`, json({ scenario }))
export const getV2Case = (id: string) => request<V2Case>(`${base}/cases/${encodeURIComponent(id)}`)
export const getV2Requirements = (id: string) => request<V2Case>(`${base}/cases/${encodeURIComponent(id)}/requirements`, json({}))
export const submitV2Transaction = (id: string, resource: V2Resource) => request<V2Case>(`${base}/cases/${encodeURIComponent(id)}/transactions/${resource}`, json({}))
export const refreshV2Transaction = (id: string, resource: V2Resource) => request<V2Case>(`${base}/cases/${encodeURIComponent(id)}/transactions/${resource}/refresh`, json({}))
export const retryV2Transaction = (id: string, resource: V2Resource) => request<V2Case>(`${base}/cases/${encodeURIComponent(id)}/transactions/${resource}/retry`, json({}))
export function uploadV2Document(id: string, kind: V2Document, file: File) {
  const body = new FormData()
  body.append('file', file)
  return request<V2Case>(`${base}/cases/${encodeURIComponent(id)}/documents/${kind}`, { method: 'POST', body })
}
