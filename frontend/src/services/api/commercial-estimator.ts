import { json, request } from './client'

const root = '/api/doctor/insurance/commercial'
export type CommercialConfiguration = { connections: Array<Record<string, unknown>>; networks: Array<Record<string, unknown>>; profiles: Array<Record<string, unknown>>; rate_versions: Array<Record<string, unknown>>; stedi_execution_mode: string; live_eligibility_enabled: boolean }
export const commercialConfiguration = () => request<CommercialConfiguration>(`${root}/configuration`)
export type CommercialAppointment = { id: string; start: string; status: string; reason: string }
export const commercialAppointments = () => request<{ appointments: CommercialAppointment[] }>(`${root}/appointments`)
export type CommercialReadiness = { ready: boolean; blockers: string[]; ecw: { payer?: string | null; plan?: string | null; scheduled_codes: Array<{ code?: string | null; units?: number | string | null }>; visit_type?: string | null; reason?: string | null } }
export const commercialReadiness = (appointment_id: string) => request<CommercialReadiness>(`${root}/appointments/readiness`, json({ appointment_id }))
export type PrevisitEstimate = {
  appointment_id: string; service_date: string; payer?: string | null; plan?: string | null;
  visit_type?: string | null; reason?: string | null; checked_at?: string | null;
  stedi_payer?: { name?: string; id: string };
  codes: Array<{ code: string; source: string; units?: number | string | null }>;
  benefits: null | { active?: boolean | null; network_status?: string; deductible_remaining_cents?: number | null;
    oop_remaining_cents?: number | null; copay_cents?: number | null; coinsurance_percent?: string | null;
    authorization_signal?: string; referral_signal?: string; normalization_notes?: string[];
    reported_options?: Array<{ kind: string; service_type_code: string; network: string; time_period: string;
      amount_cents?: number | null; percent?: string | null; payer_note?: string }> };
  price_range: null | { allowed_min_cents: number; allowed_max_cents: number;
    patient_min_cents?: number; patient_max_cents?: number;
    scenarios: Array<{ code: string; allowed_cents: number }> };
  suggestions: null | { groups: Array<{ label: string; codes: Array<{ code: string; reason?: string }> }>; notes: string[] };
  issues: string[];
}
export const commercialPrevisit = (appointment_id: string) => request<PrevisitEstimate>(`${root}/appointments/previsit`, json({ appointment_id }))
export type CommercialPastVisit = CommercialAppointment & { date: string; visit_type: string; coded_services: string[]; saved_estimate: { created_at?: string; allowed_cents?: number | null; patient_cents?: number | null; plan_cents?: number | null; disclaimer?: string | null } | null }
export const commercialHistory = () => request<{ visits: CommercialPastVisit[] }>(`${root}/history`)
export const commercialCreate = (path: 'connections' | 'networks' | 'profiles', body: object) => request<Record<string, unknown>>(`${root}/${path}`, json(body))
export const commercialStart = (body: object) => request<CommercialCase>(`${root}/cases`, json(body))
export const commercialStartFromEcw = (appointment_id: string) => request<CommercialCase>(`${root}/cases/from-ecw`, json({ appointment_id }))
export const commercialGet = (id: string) => request<CommercialCase>(`${root}/cases/${encodeURIComponent(id)}`)
export const commercialPost = (id: string, action: 'ecw-intake' | 'services' | 'evidence' | 'eligibility' | 'calculate', body: object = {}) => request<CommercialCase>(`${root}/cases/${encodeURIComponent(id)}/${action}`, json(body))
export const commercialProbeEcw = () => request<{ checked_at: string; capability_resources: string[]; resources: Record<string, { status: string; count: number | null }> }>(`${root}/ecw/saved-eligibility-probe`, json({}))
export function commercialImportRates(body: FormData) { return request<Record<string, unknown>>(`${root}/rates`, { method: 'POST', body }) }
export const commercialActivateRates = (id: string) => request<Record<string, unknown>>(`${root}/rates/${encodeURIComponent(id)}/activate`, json({}))
export type CommercialCase = { id: string; created_at: string; commercial: { payer_connection_id: string; provider_network_id: string; profile_id: string; service_date: string; service_category: string; lines: Array<{ code: string; units: number; allowed_cents?: number | null; cash_cents?: number | null }>; evidence_id: string | null; request_state: string; benefits_summary?: { active?: boolean | null; network_status?: string; deductible_remaining_cents?: number | null; oop_remaining_cents?: number | null; copay_cents?: number | null; coinsurance_percent?: string | null; authorization_signal?: string; referral_signal?: string; requires_review?: boolean }; ecw?: { appointment_date?: string; retrieved_at?: string; suggested_lines?: Array<{ code: string; units?: string | null; description?: string }>; conflicts?: string[] } }; estimate?: { allowed_cents: number; deductible_cents: number; coinsurance_cents: number; copay_cents: number; patient_cents: number; plan_cents: number; disclaimer: string } | null; review_tasks: string[] }
