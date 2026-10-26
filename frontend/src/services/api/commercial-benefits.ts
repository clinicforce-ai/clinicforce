export type PrevisitBenefits = null | {
  coverage_evidence?: {
    status: string
    reported_status: string
    start?: string | null
    end?: string | null
  }
  coverage_status_conflict?: boolean
  coverage_dates_invalid?: boolean
  service_date_matches?: boolean | null
  returned_service_dates?: { start?: string; end?: string }
  active?: boolean | null
  has_secondary?: boolean
  network_conflict?: boolean
  network_independent?: boolean
  network_status?: string
  network_source?:
    | 'clinic_record'
    | 'clinic_library_contract'
    | 'clinic_group_agreement'
    | 'clinic_nmct_policy'
    | 'clinic_library_absent'
    | 'payer_participation_message'
    | 'clinic_nonparticipation_rule'
    | 'clinic_contract_inventory'
    | null
  network_evidence_ids?: string[]
  selected_benefit_ids?: string[]
  qmb_status?: 'protected' | 'unknown'
  qmb_evidence_ids?: string[]
  network_provider_npi?: string | null
  deductible_remaining_cents?: number | null
  deductible_applies?: boolean | null
  published_plan_rule?: { label: string; url: string; effective_from: string; effective_to: string }
  published_preferred_office_copay_cents?: number
  deductible_annual_cents?: number | null
  oop_remaining_cents?: number | null
  oop_annual_cents?: number | null
  oop_annual_conflict_ids?: string[]
  oop_remaining_conflict_ids?: string[]
  oop_medical_candidate_ids?: string[]
  oop_quarantined_ids?: string[]
  copay_cents?: number | null
  copay_id?: string | null
  coinsurance_percent?: string | null
  authorization_basis?: string
  authorization_signal?: string
  referral_signal?: string
  normalization_notes?: string[]
  review_reasons?: string[]
  source_errors?: unknown[]
  requires_review?: boolean
  historical_only?: boolean
  identity?: {
    status?: string
    binding?: string | null
    match_version?: number
    reconciliation_rule?: string | null
  }
  reported_options?: Array<{
    id?: string
    procedure_code?: string
    coverage_level?: string
    plan_name?: string
    plan_index?: number
    plan_association?: string
    insurance_type?: string
    kind: string
    service_type_code: string
    places_of_service?: string[]
    network: string
    time_period: string
    effective_dates?: Record<string, string>
    amount_cents?: number | null
    percent?: string | null
    quantity?: { value?: string; qualifier?: string } | null
    payer_note?: string
  }>
}
