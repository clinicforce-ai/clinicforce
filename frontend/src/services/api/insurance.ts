export type InsuranceStage = 'intake' | 'eligibility' | 'network' | 'service' | 'authorization' | 'benefits' | 'estimate'
export type InsuranceSourceMode = 'golden' | 'documents'

export interface InsuranceCitation { source_filename: string; sha256: string; reference: string; observed_at: string }
export interface EvidenceFact { stage: InsuranceStage; label: string; value: string; citation: InsuranceCitation; source_mode: InsuranceSourceMode }
export interface InsuranceSource { id: string; filename: string; sha256: string; observed_at: string }
export interface ServiceLine {
  code: string; description: string; allowed_cents: number | null; selected: boolean; confirmed: boolean
  available?: boolean; suggested_by?: string; evidence_note?: string
}
export interface StageDecision { stage: InsuranceStage; status: 'passed'; decision: string; source_mode: InsuranceSourceMode }

export interface EstimateBreakdown {
  service_code: string; allowed_cents: number | null; nominal_coinsurance_cents: number | null
  estimate_available?: boolean; unavailable_reason?: string; service_codes?: string[]; deductible_applied_cents?: number | null; copay_cents?: number | null
  individual_deductible_remaining_cents: number; individual_oop_remaining_cents: number
  oop_cap_adjustment_cents: number | null; patient_cents: number | null; plan_cents: number | null
  source_mode: InsuranceSourceMode; calculated_at: string; disclaimer: string
}

export interface NormalizedAppointment {
  fhir_r4_resource_type: 'Appointment'; status: string; start: string | null; end: string | null; date: string
  service_type: string; appointment_type: string; reason: string; participants: string[]
  provider: string; location: string; source: InsuranceCitation
}

export interface InsuranceCase {
  id: string; source_mode: InsuranceSourceMode; status: 'review' | 'in_progress' | 'complete'; next_stage: InsuranceStage | null
  expires_at: string; ocr: { member_name: string; member_id: string; group_number: string; confirmed: boolean }
  appointment: NormalizedAppointment | null; service_lines: ServiceLine[]; stage_decisions: StageDecision[]
  facts: EvidenceFact[]; sources: InsuranceSource[]; estimate: EstimateBreakdown | null
  clinical_context?: string; documents?: Record<string, InsuranceSource & { reference?: string }>
}

export interface InsuranceEvidenceInput {
  clinic: 'NMCT'; payer: 'BCBSTX'; plan_name: string; eligibility_observed_at: string
  coverage_status: 'active' | 'inactive'; network_status: 'in_network' | 'out_of_network'
  out_of_network_benefit_status?: 'covered' | 'not_covered' | 'unknown'; out_of_network_allowed_cents?: number | null
  coinsurance_percent: number; copay_cents: number; individual_deductible_remaining_cents: number
  individual_oop_remaining_cents: number
  referral_status: 'not_required' | 'required_obtained' | 'unknown'
  authorization_status: 'not_required' | 'required_obtained' | 'unknown'
}
