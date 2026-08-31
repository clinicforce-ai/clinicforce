export interface InsuranceVerification {
  demo: true
  patient: { id: string; name: string; birthDate: string }
  appointment: { resourceType: 'Appointment'; status: string; start: string; end: string; serviceType: Array<{ text: string }>; reasonCode: Array<{ text: string }> }
  coverage_fhir: { resourceType: 'Coverage'; status: string; subscriberId: string; payor: Array<{ display: string }>; class: Array<{ value: string }> }
  card: { payer: string; member_id: string; group_number: string; plan_name: string; relationship: string }
  card_scan: { filename: string; status: string; retention: string }
  eligibility_transaction: { inquiry: { transaction_set: string; service_type: string }; response: { transaction_set: string; eligibility_code_display: string } }
  contract_match: { provider: string; npi: string; tin: string; service_location: string; source: string }
  network: { status: 'in_network' | 'out_of_network'; has_out_of_network_benefits: boolean; evidence: string }
  source_conflicts: string[]
  disclaimer: string
}

export type ReferralStatus = 'not_required' | 'verified' | 'unknown' | 'missing'
export type AuthorizationStatus = 'not_required' | 'approved' | 'pending' | 'denied'
export type InsuranceWorkbenchStage = 'eligibility' | 'network' | 'coding' | 'authorization' | 'fee_schedule' | 'calculation'

export interface ServiceEstimateInput {
  reason: string
  anticipated_service: string
  referral_status: ReferralStatus
  authorization_status: AuthorizationStatus
}

export interface ServiceEstimate {
  demo: true
  assistant: { mode: string; reason: string; anticipated_service: string | null; output: string; review_required: string }
  service: { demo_service_key: string; description: string; place_of_service: string; units: number | null; charge: number | null }
  referral: { status: ReferralStatus; source: string }
  authorization: { status: AuthorizationStatus; source: string }
  gates: string[]
  estimate: null
  disclaimer: string
}

export interface WorkbenchFact {
  label: string
  value: string
  source: string
}

export interface InsuranceWorkbenchResult {
  demo: true
  stage: InsuranceWorkbenchStage
  title: string
  status: 'passed' | 'review' | 'blocked' | 'terminal'
  source: string
  backend: { mode: string; endpoint: string }
  inputs: WorkbenchFact[]
  outputs: WorkbenchFact[]
  decision: string
  gates: string[]
  terminal_route: { code: string; label: string; detail: string } | null
  next_step: InsuranceWorkbenchStage | null
  next_label: string | null
}

export type LocalV2DocumentKind = 'insurance_card' | 'eligibility_printout' | 'provider_contract' | 'out_of_network_benefits' | 'fee_schedule' | 'authorization_requirements' | 'referral' | 'authorization'
export interface LocalV2RateLine { code: string; description: string; amount: string }
export interface LocalV2Document { id: string; kind: LocalV2DocumentKind; filename: string; evidence_pages: number[]; fields: Record<string, string>; signals: string[]; reviewed: boolean; rate_lines?: LocalV2RateLine[] }
export interface LocalV2PlanRule { status: string; message?: string; rule?: { payer: string; plan_group: string; plan_type: string; referral_required: string; referral_detail: string; prior_authorization_needed_for: string; prior_authorization_source: string; verification_notes: string } }
export interface LocalV2Case { id: string; patient_label: string; documents: LocalV2Document[]; service: string; appointment?: { reason?: string; service?: string; date_of_service?: string; place_of_service?: string }; network_status: 'unknown' | 'in_network' | 'out_of_network' | 'out_of_network_no_benefits'; plan_rule: LocalV2PlanRule; referral_status: ReferralStatus | 'unknown'; authorization_status: AuthorizationStatus | 'unknown'; stages: Record<string, LocalV2StageResult> }
export interface LocalV2StageResult { stage: string; status: 'passed' | 'blocked' | 'terminal'; decision: string; next_step: InsuranceWorkbenchStage | null; recorded_collection?: { amount: string; source: string; meaning: string }; evidence_conflicts?: string[]; inputs: { documents: Array<{ id: string; kind: string; reviewed: boolean }>; network_status?: string; plan_rule?: LocalV2PlanRule } }
