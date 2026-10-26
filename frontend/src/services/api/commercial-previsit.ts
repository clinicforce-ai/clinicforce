import type {
  BillingNote,
  RequirementEvidence,
  CollectionGuidance,
  PrevisitIdentity,
} from './commercial-common'
import type {
  BotoxReview,
  PayerAllowanceSummary,
  VisitCode,
  VisitPricedLine,
} from './commercial-visit-types'
import type { PrevisitBenefits } from './commercial-benefits'
import type { PaymentExplanation } from './commercial-explanation'
import type { CommercialNetworkEvidence } from './commercial-network-evidence'

export type PrevisitEstimate = {
  botox_review?: BotoxReview
  billing_protection?: {
    status?: 'qmb' | 'not_qmb'
    reference?: string
    verified_at?: string
  }
  payer_allowances?: PayerAllowanceSummary
  card_check?: {
    status:
      | 'read'
      | 'matched'
      | 'filled_missing'
      | 'conflict'
      | 'unverified'
      | 'not_found'
      | 'multiple_cards'
      | 'unavailable'
    source?: string | null
    failure_reason?: string | null
    fields_added?: string[]
    conflicts: string[]
    differences: string[]
    alternative_supported?: boolean
    payer_comparison?: { ecw: string; card: string }
  }
  cash_fallback?: { reason: string }
  temporary_rate_reference?: {
    source: string
    message: string
    complete: boolean
    lines: Array<{ code: string; unit_reference_cents: number; source_reference: string }>
  }
  payment_explanation?: PaymentExplanation
  evidence_resolution?: Array<{
    field: string
    status: 'resolved' | 'unresolved'
    sources: Array<{ name: string; finding: string }>
    missing_fact: string
    next_action: string
  }>
  insurance_hints?: Array<{ payer?: string | null; member_id?: string | null; sources: string[] }>
  insurance_diagnosis?: {
    coverageSource: string
    rawCandidateCount: number | null
    retainedCandidateCount: number
    verifiedCandidateCount: number
    activePolicyCount?: number | null
    identityStatus: string
    eligibilityStatus: string
    pricingStatus: string
    reasonCodes: string[]
    states: string[]
    pricingReasons: string[]
    enrollment?: { status: string; reason_code: string } | null
  }
  billing_note?: BillingNote | null
  benefit_source?: string | null
  payer_source?: string | null
  eligibility_timing?: {
    status: string
    appointment_date: string
    check_from: string
    message: string
  }
  collection_guidance?: CollectionGuidance
  encounter_summary?: {
    collect_today_cents: number | null
    estimated_total_patient_min_cents: number | null
    estimated_total_patient_max_cents: number | null
    estimated_later_balance_cents: number | null
    payments_verified_cents: number | null
    components: {
      copay_cents: number | null
      deductible_cents: number | null
      coinsurance_cents: number | null
      allowed_cap_adjustment_cents?: number | null
      oop_adjustment_cents?: number | null
    }
    coinsurance_base_cents?: number | null
    coinsurance_percent?: string | null
    coinsurance_rounded_per_line?: boolean
    unpriced_codes: string[]
    complete: boolean
    conditional: boolean
    integrity_error?:
      'patient_total_mismatch' | 'line_amount_mismatch' | 'collection_amount_mismatch' | null
  }
  requirement_evidence?: {
    referral: RequirementEvidence
    authorization: RequirementEvidence
    procedures?: Array<{
      id: string
      code: string
      referral: RequirementEvidence
      authorization: RequirementEvidence
    }>
  }
  candidate_requirements?: Array<{
    id: string
    code: string
    referral: RequirementEvidence
    authorization: RequirementEvidence
  }>
  emg_option?: {
    calculation_version?: number
    requested: boolean
    included: boolean
    additional_min_cents: number | null
    additional_max_cents: number | null
    total_min_cents: number | null
    total_max_cents: number | null
    message: string
    scenario_count: number
  }
  pricing_assumptions?: string[]
  eligibility_reconciliation?: { requires_review: boolean; message: string }
  audit_trail?: Array<{ checked_at?: string; by: string; changes: string[] }>
  insurance_fallback?: { payer?: string; member_id?: string }
  identity?: PrevisitIdentity
  planning_estimate?: {
    amount_cents: number | null
    min_cents?: number
    max_cents?: number
    collectable: false
    basis?: 'out_of_pocket_limit' | 'conditional_services'
    explanation: string
    assumptions: string[]
  } | null
  mapping_candidate?: {
    network: string
    products: string[]
    reference: string
    contract?: {
      network_name?: string
      document_id?: string
      related_document_id?: string
      locator?: string
      effective_from?: string
      effective_to?: string
    }
  }
  coverage_records?: Array<{
    id: string
    status: string
    order?: number
    member_id?: string
    group_number?: string
    subscriber?: string
    relationship?: string
    payer?: string[]
    period?: { start?: string; end?: string }
    cost_to_beneficiary?: Array<{
      type?: { text?: string }
      valueMoney?: { value: number; currency?: string }
      valueQuantity?: { value: number; unit?: string }
    }>
  }>
  patient_details?: {
    ecw_account_number?: string | null
    gender?: string
    phone?: string
    email?: string
    address?: {
      address1?: string
      address2?: string
      city?: string
      state?: string
      postalCode?: string
    }
  }
  insurance_details?: {
    payer_patient?: { firstName?: string; lastName?: string; dateOfBirth?: string }
    payer_relationship?: string
    payer_coverage_period?: { start?: string; end?: string }
    payer_subscriber?: {
      firstName?: string
      lastName?: string
      memberId?: string
      dateOfBirth?: string
    }
    member_id?: string
    member_source?: string | null
    group_number?: string
    group_source?: string | null
    subscriber_first?: string
    subscriber_last?: string
    subscriber_birth_date?: string
    relationship?: string
    record_status?: { status: string; updated_at?: string; message: string }
    coverage_period?: { start?: string; end?: string }
  }
  arrival_collection?: {
    verified_copay_cents?: number | null
    estimated_cents?: number | null
    prior_balance_cents?: number | null
    noncovered_cents?: number | null
    payments_received_cents?: number | null
    amount_cents: number | null
    reason: string
    status: string
    basis: string
  }

  practice_cash_source?: { filename: string; effective_from: string; version: string }
  payment_mode?: 'cash' | 'insurance'
  referral_evidence?: Array<{
    reference: string
    status: string
    authored_on?: string
    source: string
  }>
  cash_schedule?: {
    source: string
    effective_from: string
    packages: Array<{
      id: string
      label: string
      amount_cents: number
      indication?: string
      scope_note?: string
      filename?: string
      source?: Record<string, unknown>
    }>
  }
  cash_quote?: {
    complete: boolean
    selection: string
    package_label?: string | null
    source: string
    effective_from: string
    message: string
    min_cents?: number
    max_cents?: number
  }
  service_plan?: {
    kind: string
    groups: Array<{
      id: string
      label: string
      description: string
      selection_mode: string
      role: string
      ids: string[]
    }>
    recommended_ids: string[]
    recommendation_basis: string
    history_count: number
    selection_source?: string
    method?: string
    assumptions?: string[]
    recommended_units?: Record<string, number>
  }
  ecw_saved_eligibility?: {
    status: string
    reason_code?: string
    active?: boolean
    message: string
    checked_at?: string
    reference?: string
  }
  appointment_context?: {
    appointment_type?: string
    timezone?: string
    notes_status?: string
    imported_fields?: Array<{ label: string; text: string; source: string; updated_at?: string }>
    location?: string
    rendering_provider?: { name?: string; npi?: string }
    appointment_status?: string
  }
  ecw_coverage_active?: boolean
  library_pricing?: Array<{
    document_id: string
    label: string
    payer?: string
    summary?: {
      status: string
      usable_rules?: number
      issues?: Array<{ status: string; reason: string; locator?: string }>
    }
  }>
  coverage_status?: 'VERIFIED' | 'REVIEW_REQUIRED' | 'NOT_FOUND'
  recovery?: {
    state: string
    outcome?: string
    message: string
    started_at?: string
    checked_at?: string
    reference?: string
    coverage_count?: number
    stage?: string
    reason_code?: string
    exhausted?: boolean
    steps?: Record<string, string>
    coverage_status?: 'VERIFIED' | 'REVIEW_REQUIRED' | 'NOT_FOUND'
    card_status?: string
    attempt_count?: number
    payer_routes?: Array<{
      id: string
      name: string
      payer_id?: string
      aliases: string[]
      names: string[]
      match_basis: string
      eligibility?: string
    }>
    raw_candidate_count?: number
    active_policy_count?: number | null
    retained_candidate_count?: number
    verified_candidate_count?: number
    candidates?: Array<{
      id?: string
      policy_group_id?: string
      policy_response_count?: number
      payer: string
      payer_identifier?: string
      plans?: string[]
      relationship: string
      address_matches: boolean
      matches_ecw_policy?: boolean
      group_number?: string | null
      coverage_evidence?: {
        status: string
        reported_status: string
        start?: string | null
        end?: string | null
      }
      identity_status?: string
      inquiry_eligible?: boolean
      member_id?: string | null
      subscriber?: {
        firstName?: string | null
        lastName?: string | null
        dateOfBirth?: string | null
      }
      dependent?: {
        firstName?: string | null
        lastName?: string | null
        dateOfBirth?: string | null
      } | null
      source?: string
      state?: string
      verification_status?: string
      reason_codes?: string[]
    }>
  }
  coordination?: {
    state?: string
    status?: string
    reason_code?: string
    missing_fields?: string[]
    source_fields?: string[]
    checked_at?: string
    primacy_determined?: boolean
    coverage_overlap?: boolean
    classification?: string
    payer_roles?: Array<{ role: string; payer_name: string }>
  }
  eligibility_failure?: {
    kind: string
    codes: string[]
    title: string
    detail: string
    action: string
    attempted_at: string
  }
  enrollment?: { status: string; route?: string }
  eligibility_error?: string
  context_token?: string
  revision?: number
  selected_ids?: string[]
  selected_service_count?: number
  provisional_scenario?: {
    kind: 'office_level' | 'ncs_study_count'
    count: number
    candidate_ids: string[]
    basis: string
  }
  service_selection_status?: 'preview' | 'approved'
  available_codes?: VisitCode[]
  plan_details?: {
    ecw: Record<string, string[]>
    stedi: {
      names?: string[]
      supplementary_names?: string[]
      network_names?: string[]
      identified_product?: string | null
      product_evidence?: Record<string, string> | null
      plan_number?: string
      group_number?: string
      group_name?: string | null
      verification_payer?: { name: string; payer_id: string; evidence_path: string } | null
      source?: string
    }
  }
  calculated_at?: string
  ecw_retrieved_at?: string
  response_reference?: string
  network_evidence?: CommercialNetworkEvidence
  agreement_exclusion?: {
    decision: 'excluded_under_available_agreement'
    document_id: string
    product_kind?: 'local_medicare_advantage_ppo' | 'regional_medicare_advantage_ppo'
    page: number
    passage: string
    effective_from?: string
    effective_to?: string
  }
  check_id?: string
  appointment_id: string
  service_date: string
  payer?: string | null
  plan?: string | null
  visit_type?: string | null
  reason?: string | null
  checked_at?: string | null
  stedi_payer?: { name?: string; id: string }
  codes: VisitCode[]
  benefits: PrevisitBenefits
  price_range: null | {
    cash_complete?: boolean
    cash_min_cents?: number
    cash_max_cents?: number
    complete?: boolean
    conditional?: boolean
    mode?: string
    service_issues?: string[]
    lines?: VisitPricedLine[]
    collection?: {
      action: string
      amount_cents?: number | null
      reason: string
      payment_reference?: string
      verified_at?: string
    }
    allowed_min_cents?: number
    allowed_max_cents?: number
    patient_min_cents?: number
    patient_max_cents?: number
    plan_min_cents?: number
    plan_max_cents?: number
    scenarios: Array<{ code: string; allowed_cents: number }>
  }
  suggestions: null | {
    groups: Array<{ label: string; codes: Array<{ code: string; reason?: string }> }>
    notes: string[]
  }
  issues: string[]
}
