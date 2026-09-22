import { json, request } from './client'

const root = '/api/doctor/insurance/commercial'
export type CommercialConfiguration = { connections: Array<Record<string, unknown>>; networks: Array<Record<string, unknown>>; profiles: Array<Record<string, unknown>>; rate_versions: Array<Record<string, unknown>>; stedi_execution_mode: string; live_eligibility_enabled: boolean }
export const commercialConfiguration = () => request<CommercialConfiguration>(`${root}/configuration`)
export const commercialCreate = (path: 'connections' | 'networks' | 'profiles', body: object) => request<Record<string, unknown>>(`${root}/${path}`, json(body))
export const commercialStart = (body: object) => request<CommercialCase>(`${root}/cases`, json(body))
export const commercialGet = (id: string) => request<CommercialCase>(`${root}/cases/${encodeURIComponent(id)}`)
export const commercialPost = (id: string, action: 'services' | 'evidence' | 'eligibility' | 'calculate', body: object = {}) => request<CommercialCase>(`${root}/cases/${encodeURIComponent(id)}/${action}`, json(body))
export function commercialImportRates(body: FormData) { return request<Record<string, unknown>>(`${root}/rates`, { method: 'POST', body }) }
export const commercialActivateRates = (id: string) => request<Record<string, unknown>>(`${root}/rates/${encodeURIComponent(id)}/activate`, json({}))
export type CommercialCase = { id: string; created_at: string; commercial: { payer_connection_id: string; provider_network_id: string; profile_id: string; service_date: string; service_category: string; lines: Array<{ code: string; units: number; allowed_cents?: number | null; cash_cents?: number | null }>; evidence_id: string | null; request_state: string }; estimate?: { allowed_cents: number; deductible_cents: number; coinsurance_cents: number; copay_cents: number; patient_cents: number; plan_cents: number; disclaimer: string } | null; review_tasks: string[] }
