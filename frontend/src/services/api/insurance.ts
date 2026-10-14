export type InsuranceStage =
  'intake' | 'eligibility' | 'network' | 'service' | 'authorization' | 'benefits' | 'estimate'
type InsuranceSourceMode = 'documents'

interface InsuranceCitation {
  source_filename: string
  sha256: string
  reference: string
  observed_at: string
  quote?: string
}
interface EvidenceFact {
  stage: InsuranceStage
  label: string
  value: string
  explanation?: string
  citation: InsuranceCitation
  source_mode: InsuranceSourceMode
}
interface InsuranceCoverageSuggestions {
  plan_name?: string
  network_status?: 'in_network' | 'out_of_network'
  coinsurance_percent?: number
  deductible_remaining_cents?: number
  oop_remaining_cents?: number
}
interface InsuranceSource {
  id: string
  filename: string
  sha256: string
  observed_at: string
  reference?: string
  document_dates?: Array<{ label: string; value: string; page: number }>
  findings?: {
    clinical_context?: string
    clinical_excerpts?: Array<{ page: number; quote: string; category: string }>
    visit_type?: 'new' | 'established' | null
    policy_terms?: Array<{ page: number; quote: string; reference: string }>
    suggested_coverage?: InsuranceCoverageSuggestions
    rate_count?: number
    pages?: Array<{ page: number; text: string }>
    authorizations?: Array<{
      code: string
      status: 'not_required' | 'required' | 'not_stated'
      reference: string
    }>
  }
}
interface ServiceLine {
  group_id?: string
  requires_quantity?: boolean
  unit_label?: string
  units?: number
  unit_allowed_cents?: number | null
  referral_status?: string
  referral_reference?: string
  requirement_evidence?: Array<{
    kind: string
    status: string
    network: string
    quote: string
    reference: string
    source_filename: string
    observed_at: string
  }>
  code: string
  description: string
  allowed_cents: number | null
  selected: boolean
  confirmed: boolean
  available?: boolean
  suggested_by?: string
  evidence_note?: string
  plain_description?: string
  recommendation_reason?: string
  service_item_id?: string
  service_item_name?: string
  service_status?: 'scheduled' | 'if_elected'
  source_document?: string
  reference?: string
  select_together?: boolean
  authorization_status?: string
  authorization_reference?: string
}
interface ServiceGroup {
  id: string
  label: string
  category: string
  selection_mode: 'single' | 'multiple'
  description: string
  codes: string[]
  requires_groups: string[]
}
interface StageDecision {
  stage: InsuranceStage
  status: 'passed'
  decision: string
  source_mode: InsuranceSourceMode
}

interface EstimateBreakdown {
  service_code: string
  allowed_cents: number | null
  nominal_coinsurance_cents: number | null
  estimate_available?: boolean
  unavailable_reason?: string
  service_codes?: string[]
  deductible_applied_cents?: number | null
  copay_cents?: number | null
  line_items?: Array<{
    code: string
    description: string
    allowed_cents: number | null
    unit_allowed_cents?: number | null
    units?: number
    deductible_applied_cents?: number
    coinsurance_cents?: number | null
    patient_cents?: number
    plan_cents?: number
  }>
  individual_deductible_remaining_cents: number
  individual_oop_remaining_cents: number
  oop_cap_adjustment_cents: number | null
  patient_cents: number | null
  plan_cents: number | null
  source_mode: InsuranceSourceMode
  calculated_at: string
  disclaimer: string
  plain_explanation?: string
  collect_today_cents?: number | null
  collection_action?: 'collect' | 'do_not_collect' | 'verify_before_collecting' | 'out_of_network'
  confidence?: 'high' | 'medium' | 'low'
  collection_reason?: string
  normal_patient_share_cents?: number | null
  verification_reasons?: string[]
  self_pay?: {
    available: boolean
    total_cents: number | null
    line_items: Array<{ code: string; amount_cents: number | null }>
  }
  confidence_reason?: string
  scheduling_action?: 'do_not_schedule' | 'verify_network_and_rates'
  service_item_estimates?: Array<{
    id: string
    name: string
    status: 'scheduled' | 'if_elected'
    codes: string[]
    estimate_available: boolean
    allowed_cents: number | null
    normal_patient_share_cents: number | null
    oop_adjustment_cents: number | null
    patient_cents: number | null
    plan_cents: number | null
  }>
}

export interface InsuranceCase {
  id: string
  source_mode: InsuranceSourceMode
  status: 'review' | 'in_progress' | 'complete'
  next_stage: InsuranceStage | null
  created_at: string
  expires_at: string | null
  ocr: InsuranceCardFields
  service_lines: ServiceLine[]
  stage_decisions: StageDecision[]
  facts: EvidenceFact[]
  sources: InsuranceSource[]
  estimate: EstimateBreakdown | null
  clinical_context?: string
  documents?: Record<string, InsuranceSource>
  visit_type?: 'new' | 'established' | null
  service_groups?: ServiceGroup[]
  suggestions_reviewed?: boolean
  suggestion_notes?: string[]
  card_source?: InsuranceSource
  ocr_evidence?: Record<
    string,
    {
      page: number
      quote: string
      value: string
      document_id?: string
      source_filename?: string
      source_page?: number
    }
  >
}

interface InsuranceCardFields {
  member_name: string
  member_id: string
  group_number: string
  confirmed: boolean
  plan_type?: string
  coverage_date?: string
  coinsurance?: string
  in_network_deductible?: string
  out_of_network_deductible?: string
  in_network_oop_maximum?: string
  out_of_network_oop_maximum?: string
  rx_bin?: string
  rx_pcn?: string
  rx_group?: string
  preauth_medical_phone?: string
  mdlive_phone?: string
  nurseline_phone?: string
  customer_service_phone?: string
  provider_service_phone?: string
  benefit_advocate_phone?: string
  eap_phone?: string
  pharmacy_phone?: string
}
