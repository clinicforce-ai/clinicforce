import { json, request } from './client'

const root = '/api/doctor/insurance/commercial'
export type CommercialConfiguration = { connections: Array<Record<string, unknown>>; networks: Array<Record<string, unknown>>; profiles: Array<Record<string, unknown>>; rate_versions: Array<Record<string, unknown>>; stedi_execution_mode: string; live_eligibility_enabled: boolean }
export const commercialConfiguration = () => request<CommercialConfiguration>(`${root}/configuration`)
export type CommercialAppointment = { id: string; start: string; status: string; reason: string }
export const commercialAppointments = () => request<{ appointments: CommercialAppointment[] }>(`${root}/appointments`)
export type CommercialReadiness = { ready: boolean; blockers: string[]; ecw: { payer?: string | null; plan?: string | null; scheduled_codes: Array<{ code?: string | null; units?: number | string | null }>; visit_type?: string | null; reason?: string | null } }
export const commercialReadiness = (appointment_id: string) => request<CommercialReadiness>(`${root}/appointments/readiness`, json({ appointment_id }))
export type PrevisitEstimate = {
  identity?: { status: 'matched' | 'review' | 'reconciled'; binding: string; can_confirm: boolean; differences: Array<{ field: string; ecw: string; payer: string }>; evidence?: { reference: string; verified_at: string } };
  planning_estimate?: { amount_cents: number; collectable: false; explanation: string; assumptions: string[] } | null;
  mapping_candidate?: { network: string; products: string[]; reference: string };
  coverage_records?: Array<{ id: string; status: string; order?: number; member_id?: string; group_number?: string; subscriber?: string; relationship?: string; payer?: string[]; period?: { start?: string; end?: string }; cost_to_beneficiary?: Array<{ type?: { text?: string }; valueMoney?: { value: number; currency?: string }; valueQuantity?: { value: number; unit?: string } }> }>;
  patient_details?: { gender?: string; phone?: string; email?: string; address?: { address1?: string; address2?: string; city?: string; state?: string; postalCode?: string } };
  insurance_details?: { payer_patient?: { firstName?: string; lastName?: string; dateOfBirth?: string }; payer_relationship?: string; payer_coverage_period?: { start?: string; end?: string }; payer_subscriber?: { firstName?: string; lastName?: string; memberId?: string; dateOfBirth?: string }; member_id?: string; group_number?: string; subscriber_first?: string; subscriber_last?: string; subscriber_birth_date?: string; relationship?: string; record_status?: { status: string; updated_at?: string; message: string }; coverage_period?: { start?: string; end?: string } };
  arrival_collection?: { verified_copay_cents?: number | null; estimated_cents?: number | null; prior_balance_cents?: number | null; noncovered_cents?: number | null; payments_received_cents?: number | null; amount_cents: number | null; reason: string; status: string; basis: string };

  practice_cash_source?: { filename: string; effective_from: string; version: string };
  payment_mode?: 'cash' | 'insurance';
  referral_evidence?: Array<{ reference: string; status: string; authored_on?: string; source: string }>;
  cash_schedule?: { source: string; sha256: string; effective_from: string; packages: Array<{ id: string; label: string; amount_cents: number; max_limbs: number; ncs_example: string; emg_example: string; limbs_example: number }>; package_includes: string; proposed: string };
  cash_quote?: { complete: boolean; selection: string; source: string; effective_from: string; message: string; min_cents?: number; max_cents?: number };
  service_plan?: { kind: string; groups: Array<{ id: string; label: string; description: string; selection_mode: string; role: string; ids: string[] }>; recommended_ids: string[]; recommendation_basis: string; history_count: number; selection_source?: string; method?: string; assumptions?: string[]; recommended_units?: Record<string, number> };
  ecw_saved_eligibility?: { status: string; active?: boolean; message: string; checked_at?: string; reference?: string };
  appointment_context?: { timezone?: string; notes_status?: string; imported_fields?: Array<{ label: string; text: string; source: string; updated_at?: string }>; location?: string; rendering_provider?: { name?: string; npi?: string }; appointment_status?: string };
  ecw_coverage_active?: boolean;
  recovery?: { state: string; outcome?: string; message: string; started_at?: string; checked_at?: string; reference?: string; coverage_count?: number;
    candidates?: Array<{ id: string; payer: string; payer_identifier?: string; plans: string[]; relationship: string; address_matches: boolean }> };
  eligibility_failure?: { kind: string; codes: string[]; title: string; detail: string; action: string; attempted_at: string };
  eligibility_error?: string; context_token?: string; revision?: number; selected_ids?: string[]; available_codes?: VisitCode[];
  plan_details?: { ecw: Record<string, string[]>; stedi: { names?: string[]; plan_number?: string; group_number?: string } };
  calculated_at?: string; ecw_retrieved_at?: string; response_reference?: string; network_evidence?: { network_name?: string; product_source?: string; evidence_reference?: string; effective_from?: string; effective_to?: string };
  check_id?: string; appointment_id: string; service_date: string; payer?: string | null; plan?: string | null;
  visit_type?: string | null; reason?: string | null; checked_at?: string | null;
  stedi_payer?: { name?: string; id: string };
  codes: VisitCode[];
  benefits: null | { service_date_matches?: boolean | null; returned_service_dates?: { start?: string; end?: string }; active?: boolean | null; network_status?: string; deductible_remaining_cents?: number | null;
    oop_remaining_cents?: number | null; copay_cents?: number | null; coinsurance_percent?: string | null;
    authorization_basis?: string; authorization_signal?: string; referral_signal?: string; normalization_notes?: string[]; review_reasons?: string[]; requires_review?: boolean;
    reported_options?: Array<{ id?: string; procedure_code?: string; coverage_level?: string; plan_name?: string; kind: string; service_type_code: string; network: string; time_period: string;
      amount_cents?: number | null; percent?: string | null; quantity?: { value?: string; qualifier?: string } | null; payer_note?: string }> };
  price_range: null | { cash_complete?: boolean; cash_min_cents?: number; cash_max_cents?: number; complete?: boolean; conditional?: boolean; mode?: string; service_issues?: string[]; lines?: VisitPricedLine[]; collection?: { action: string; amount_cents?: number | null; reason: string; payment_reference?: string; verified_at?: string }; allowed_min_cents?: number; allowed_max_cents?: number;
    patient_min_cents?: number; patient_max_cents?: number; plan_min_cents?: number; plan_max_cents?: number;
    scenarios: Array<{ code: string; allowed_cents: number }> };
  suggestions: null | { groups: Array<{ label: string; codes: Array<{ code: string; reason?: string }> }>; notes: string[] };
  issues: string[];
}
export const commercialPrevisit = (appointment_id: string) => request<PrevisitEstimate>(`${root}/appointments/previsit`, json({ appointment_id }))
export const commercialSavedPrevisit = (id: string) => request<PrevisitEstimate>(`${root}/cases/${encodeURIComponent(id)}/previsit`)
export const commercialSelectCoverageCandidate = (result: PrevisitEstimate, candidate_id: string) => request<PrevisitEstimate>(`${root}/cases/${encodeURIComponent(result.check_id!)}/coverage-candidate`, json({ candidate_id, revision: result.revision, context_token: result.context_token }))
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
export type CommercialCase = { id: string; created_at: string; commercial: { payer_connection_id: string; provider_network_id: string; profile_id: string; service_date: string; service_category: string; lines: Array<{ code: string; units: number; allowed_cents?: number | null; cash_cents?: number | null }>; evidence_id: string | null; request_state: string; benefits_summary?: { active?: boolean | null; network_status?: string; deductible_remaining_cents?: number | null; oop_remaining_cents?: number | null; copay_cents?: number | null; coinsurance_percent?: string | null; authorization_basis?: string; authorization_signal?: string; referral_signal?: string; requires_review?: boolean }; ecw?: { appointment_date?: string; retrieved_at?: string; suggested_lines?: Array<{ code: string; units?: string | null; description?: string }>; conflicts?: string[] } }; estimate?: { allowed_cents: number; deductible_cents: number; coinsurance_cents: number; copay_cents: number; patient_cents: number; plan_cents: number; disclaimer: string } | null; review_tasks: string[] }

export type VisitCode = { id?: string; code: string; source: string; description?: string; units?: number | string | null; modifiers?: string[]; unit_allowed_cents?: number | null; unit_cash_cents?: number | null; rate_source?: string }
export type VisitRequirement = { required: string; status: string; cleared: boolean; reference?: string; verified_at?: string; verified_by?: string; valid_to?: string }
export type VisitPricedLine = VisitCode & {
  id: string; allowed_cents?: number | null; unit_allowed_cents?: number | null;
  cash_cents?: number | null; cash_source?: string; cash_included?: boolean;
  benefit_verification?: { reference: string; notes: string; verified_by: string; verified_at: string; valid_from: string; valid_to: string } | null;
  rate_source: string; rate_version?: string; rate_effective_from?: string; benefit_basis: string; issues: string[]; conditions?: string[];
  benefits: NonNullable<PrevisitEstimate['benefits']> & { copay_period?: string; benefit_label?: string; benefit_summary?: string; visits_remaining?: number; deductible_applies?: boolean; policy_evidence?: { title: string; url: string; section: string }; requirements: { referral: VisitRequirement; authorization: VisitRequirement } };
  calculation: null | { cost_share?: string; allowed_cents: number; patient_cents: number; plan_cents: number; deductible_cents: number; copay_cents: number; coinsurance_cents: number; oop_adjustment_cents: number }
}
export type VisitReview = { identity_confirmation?: { binding: string; reference: string; notes: string }; product_mapping?: { reference: string; notes: string; valid_from: string; valid_to: string }; payment_mode?: 'cash' | 'insurance'; cash_selection?: string; selected_ids?: string[]; confirmed_units?: Record<string, number>; payments_received_cents?: number; payment_reference?: string; verification?: {
  line_id: string; kind: 'referral' | 'authorization' | 'benefits'; status: string; reference: string; notes: string;
  valid_from: string; valid_to: string; units: number; selected_ids?: string[]; cost_share?: string; deductible_applies?: boolean
} }
export const commercialVisitReview = (result: PrevisitEstimate, review: VisitReview) => request<PrevisitEstimate>(`${root}/cases/${encodeURIComponent(result.check_id!)}/visit-review`, json({ ...review, revision: result.revision, context_token: result.context_token }))

export type CashScheduleDraft = { id: string; filename: string; effective_from: string; rows: Array<{ code: string; description: string; cash_cents: number }> }
export function commercialUploadCashSchedule(file: File, effective: string) {
  const body = new FormData(); body.append('file', file)
  return request<CashScheduleDraft>(`${root}/cash-schedules?effective_from=${encodeURIComponent(effective)}`, { method: 'POST', body })
}
export const commercialActivateCashSchedule = (id: string) => request<{ id: string }>(`${root}/cash-schedules/${encodeURIComponent(id)}/activate`, json({}))

export type VisitFinancials = { source: string; checked_at: string; status: string; partial: boolean; message: string; transactions_checked: number; lines: Array<{ transaction_id: string; claim_reference?: string; line_index: number; claim_status?: string; code: string; modifiers: string[]; service_date?: string; payer?: string; allowed_cents: number | null; insurer_paid_cents: number | null; patient_responsibility_cents: number | null; source: string; received_at: string }> }
export const commercialFinancials = (id: string) => request<VisitFinancials>(`${root}/cases/${encodeURIComponent(id)}/financials`)
