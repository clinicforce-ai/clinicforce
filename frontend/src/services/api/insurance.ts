export type InsuranceStage = 'intake' | 'eligibility' | 'network' | 'service' | 'authorization' | 'benefits' | 'estimate'
export type InsuranceSourceMode = 'golden' | 'documents'

export interface InsuranceCitation { source_filename: string; sha256: string; reference: string; observed_at: string }
export interface EvidenceFact { stage: InsuranceStage; label: string; value: string; explanation?: string; citation: InsuranceCitation; source_mode: InsuranceSourceMode }
export interface InsuranceCoverageSuggestions {
  plan_name?: string; coverage_status?: 'active'; network_status?: 'in_network' | 'out_of_network'
  out_of_network_benefit_status?: 'covered' | 'not_covered'
  coinsurance_percent?: number; deductible_remaining_cents?: number; oop_remaining_cents?: number
}
export interface InsuranceSource {
  id: string; filename: string; sha256: string; observed_at: string; reference?: string
  findings?: {
    pages_scanned?: number; suggested_coverage?: InsuranceCoverageSuggestions
    rate_count?: number; network?: string; generated_at?: string
    visit_type?: 'new' | 'established'; clinical_context?: string; use?: string
    cash_packages?: Array<{ name: string; best_for: string; price_cents: number; page: number }>
    authorizations?: Array<{ code: string; status: 'not_required' | 'required' | 'unknown'; page: number; reference: string }>
    referral?: { required: boolean; status: 'received' | 'pending' | 'not_stated'; reference?: string; effective_date?: string; expiration_date?: string; page?: number }
  }
}
export interface ServiceLine {
  code: string; description: string; allowed_cents: number | null; selected: boolean; confirmed: boolean
  available?: boolean; suggested_by?: string; evidence_note?: string; plain_description?: string
  service_item_id?: string; service_item_name?: string; service_status?: 'scheduled' | 'if_elected'
  select_together?: boolean
  recommendation_reason?: string; authorization_status?: 'not_required' | 'required' | 'unknown' | 'not_found'; authorization_reference?: string
}
export interface StageDecision { stage: InsuranceStage; status: 'passed'; decision: string; source_mode: InsuranceSourceMode }

export interface EstimateBreakdown {
  service_code: string; allowed_cents: number | null; nominal_coinsurance_cents: number | null
  estimate_available?: boolean; unavailable_reason?: string; service_codes?: string[]; deductible_applied_cents?: number | null; copay_cents?: number | null
  line_items?: Array<{ code: string; description: string; allowed_cents: number | null }>
  individual_deductible_remaining_cents: number; individual_oop_remaining_cents: number
  oop_cap_adjustment_cents: number | null; patient_cents: number | null; plan_cents: number | null
  source_mode: InsuranceSourceMode; calculated_at: string; disclaimer: string
  plain_explanation?: string
  collect_today_cents?: number; collection_action?: 'collect' | 'do_not_collect'; confidence?: 'high' | 'medium'
  confidence_reason?: string
  scheduling_action?: 'do_not_schedule' | 'verify_network_and_rates'
  service_item_estimates?: Array<{
    id: string; name: string; status: 'scheduled' | 'if_elected'; codes: string[]; estimate_available: boolean
    allowed_cents: number | null; normal_patient_share_cents: number | null; oop_adjustment_cents: number | null
    patient_cents: number | null; plan_cents: number | null
  }>
}

export interface NormalizedAppointment {
  fhir_r4_resource_type: 'Appointment'; status: string; start: string | null; end: string | null; date: string
  service_type: string; appointment_type: string; reason: string; participants: string[]
  provider: string; location: string; source: InsuranceCitation
}

export interface InsuranceCase {
  id: string; source_mode: InsuranceSourceMode; status: 'review' | 'in_progress' | 'complete'; next_stage: InsuranceStage | null
  payment_path?: 'insurance' | 'self_pay'; created_at: string; expires_at: string; ocr: InsuranceCardFields
  appointment: NormalizedAppointment | null; service_lines: ServiceLine[]; stage_decisions: StageDecision[]
  facts: EvidenceFact[]; sources: InsuranceSource[]; estimate: EstimateBreakdown | null
  clinical_context?: string; visit_type?: 'new' | 'established'; documents?: Record<string, InsuranceSource>
}

export interface InsuranceCardFields {
  member_name: string; member_id: string; group_number: string; confirmed: boolean
  plan_type?: string; coverage_date?: string; coinsurance?: string
  in_network_deductible?: string; out_of_network_deductible?: string
  in_network_oop_maximum?: string; out_of_network_oop_maximum?: string
  rx_bin?: string; rx_pcn?: string; rx_group?: string
  preauth_medical_phone?: string; mdlive_phone?: string; nurseline_phone?: string
  customer_service_phone?: string; provider_service_phone?: string; benefit_advocate_phone?: string
  eap_phone?: string; pharmacy_phone?: string
}
