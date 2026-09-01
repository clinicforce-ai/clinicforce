export type InsuranceStage = 'intake' | 'eligibility' | 'network' | 'service' | 'authorization' | 'benefits' | 'estimate'

export interface InsuranceCitation { source_filename: string; sha256: string; reference: string; observed_at: string }
export interface EvidenceFact { stage: InsuranceStage; label: string; value: string; citation: InsuranceCitation; source_mode: 'golden' }
export interface InsuranceSource { id: string; filename: string; sha256: string; observed_at: string }
export interface ServiceLine { code: '99204' | '99205'; description: string; allowed_cents: number; selected: boolean; confirmed: boolean }
export interface StageDecision { stage: InsuranceStage; status: 'passed'; decision: string; source_mode: 'golden' }

export interface EstimateBreakdown {
  service_code: string; allowed_cents: number; nominal_coinsurance_cents: number
  individual_deductible_remaining_cents: number; individual_oop_remaining_cents: number
  oop_cap_adjustment_cents: number; patient_cents: number; plan_cents: number
  source_mode: 'golden'; calculated_at: string; disclaimer: string
}

export interface NormalizedAppointment {
  fhir_r4_resource_type: 'Appointment'; status: string; start: string | null; end: string | null; date: string
  service_type: string; appointment_type: string; reason: string; participants: string[]
  provider: string; location: string; source: InsuranceCitation
}

export interface InsuranceCase {
  id: string; source_mode: 'golden'; status: 'review' | 'in_progress' | 'complete'; next_stage: InsuranceStage | null
  expires_at: string; ocr: { member_name: string; member_id: string; group_number: string; confirmed: boolean }
  appointment: NormalizedAppointment | null; service_lines: ServiceLine[]; stage_decisions: StageDecision[]
  facts: EvidenceFact[]; sources: InsuranceSource[]; estimate: EstimateBreakdown | null
}
