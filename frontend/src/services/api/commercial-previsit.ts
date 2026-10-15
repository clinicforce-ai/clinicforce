import type { BillingNote, RequirementEvidence, CollectionGuidance } from './commercial-common'
import type { VisitCode, VisitPricedLine } from './commercial-visit-types'

export type PrevisitEstimate = {
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
    fields_added: string[]
    conflicts: string[]
    differences: string[]
    alternative_supported?: boolean
    payer_comparison?: { ecw: string; card: string }
  }
  cash_fallback?: { reason: string }
  payment_explanation?: {
    sentence: string
    reason_code: string
    network_tier: string
    pricing_basis: string
    unpriced_codes: string[]
    known_amount_only: boolean
    notice?: string | null
  }
  insurance_hints?: Array<{ payer?: string | null; member_id?: string | null; sources: string[] }>
  insurance_diagnosis?: {
    coverageSource: string
    rawCandidateCount: number | null
    retainedCandidateCount: number
    verifiedCandidateCount: number
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
    }
    unpriced_codes: string[]
    complete: boolean
    conditional: boolean
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
  identity?: {
    status: 'matched' | 'warning' | 'review' | 'reconciled'
    message?: string
    binding: string
    can_confirm: boolean
    differences: Array<{ field: string; ecw: string; payer: string }>
    evidence?: { reference: string; verified_at: string }
  }
  planning_estimate?: {
    amount_cents: number
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
    retained_candidate_count?: number
    verified_candidate_count?: number
    candidates?: Array<{
      id: string
      payer: string
      payer_identifier?: string
      plans: string[]
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
  eligibility_failure?: {
    kind: string
    codes: string[]
    title: string
    detail: string
    action: string
    attempted_at: string
  }
  eligibility_error?: string
  context_token?: string
  revision?: number
  selected_ids?: string[]
  available_codes?: VisitCode[]
  plan_details?: {
    ecw: Record<string, string[]>
    stedi: {
      names?: string[]
      network_names?: string[]
      plan_number?: string
      group_number?: string
      source?: string
    }
  }
  calculated_at?: string
  ecw_retrieved_at?: string
  response_reference?: string
  network_evidence?: {
    network_name?: string
    product_source?: string
    evidence_reference?: string
    effective_from?: string
    effective_to?: string
    document_id?: string
    document_label?: string
    locator?: string
    related_document_id?: string
    related_document_label?: string
    reason?: string
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
  benefits: null | {
    coverage_evidence?: {
      status: string
      reported_status: string
      start?: string | null
      end?: string | null
    }
    coverage_status_conflict?: boolean
    service_date_matches?: boolean | null
    returned_service_dates?: { start?: string; end?: string }
    active?: boolean | null
    network_status?: string
    network_source?:
      | 'clinic_record'
      | 'clinic_library_contract'
      | 'clinic_library_absent'
      | 'payer_participation_message'
      | null
    network_evidence_ids?: string[]
    deductible_remaining_cents?: number | null
    oop_remaining_cents?: number | null
    copay_cents?: number | null
    copay_id?: string | null
    coinsurance_percent?: string | null
    authorization_basis?: string
    authorization_signal?: string
    referral_signal?: string
    normalization_notes?: string[]
    review_reasons?: string[]
    requires_review?: boolean
    reported_options?: Array<{
      id?: string
      procedure_code?: string
      coverage_level?: string
      plan_name?: string
      kind: string
      service_type_code: string
      network: string
      time_period: string
      amount_cents?: number | null
      percent?: string | null
      quantity?: { value?: string; qualifier?: string } | null
      payer_note?: string
    }>
  }
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
