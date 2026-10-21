export type CommercialAppointment = { id: string; start: string; status: string; reason: string }

export type PrevisitIdentity = {
  status: 'matched' | 'warning' | 'review' | 'reconciled'
  match_version?: number
  missing_fields?: string[]
  message?: string
  binding: string | null
  can_confirm: boolean
  differences: Array<{ field: string; ecw: string; payer: string }>
  policy_differences?: Array<{ field: string; ecw: string; payer: string }>
  evidence?: { reference: string; verified_at: string }
}

export type BillingNote = {
  kind: 'no_collection' | 'office_copay' | 'self_pay'
  amount_cents?: number
  source: string
  updated_at: string
  text: string
}

export type RequirementEvidence = {
  status: 'Required' | 'Not required' | 'Verification required'
  reason: string
  verify_url?: string
  verification?: string
  contact?: string
  approval_status?: string
  referral_obtained?: 'YES' | 'NO' | 'UNKNOWN' | 'NOT APPLICABLE'
  pa_approval?: 'APPROVED' | 'PENDING' | 'DENIED' | 'EXPIRED' | 'UNVERIFIED' | 'NOT APPLICABLE'
  member_specific_check?: boolean
  applicable_scope?: {
    payer_product?: string
    codes?: string[]
    service_categories?: string[]
    units_by_code?: Record<string, number | null | undefined>
    indication?: string | null
    diagnoses?: unknown
    network_tier?: string
    rendering_npi?: string
    billing_npi?: string
    tin?: string
    location_id?: string
    place_of_service?: string
    service_date?: string
  }
  approval_reference?: string
  approval_verified_at?: string
  sources: Array<{
    label?: string
    filename?: string
    page?: number
    document_id?: string
    url?: string
    source_type?: string
    source_version?: string
    effective_from?: string
    effective_to?: string
    verified_at?: string
  }>
}

export type CollectionGuidance = {
  billing_note?: BillingNote
  amount_cents: number | null
  range_min_cents?: number | null
  range_max_cents?: number | null
  status: string
  basis: string
  integrity_error?: 'patient_total_mismatch' | 'line_amount_mismatch' | 'collection_amount_mismatch'
  reason: string
  patient_message: string
  assumptions: string[]
  patient_actions: string[]
  policy_setup_required?: boolean
  positive_advance_policy_missing?: boolean
  published_copay_policy_missing?: boolean
  deferred_supported_share?: boolean
  collection_rule?: { mode: string; reference: string; approved_at: string }
  network: string
  network_source?: string
  requirement_sources?: { referral?: string; authorization?: string }
  requirements: { referral: string; authorization: string }
  services: Array<{
    id: string
    code: string
    benefit: string
    benefit_source?: string | null
    patient_cents: number | null
    oop_remaining_cents?: number | null
    copay_cents?: number | null
    coinsurance_percent?: string | null
    deductible_remaining_cents?: number | null
    deductible_annual_cents?: number | null
    deductible_applicability?: 'DOES_NOT_APPLY' | 'APPLIES' | 'ALREADY_MET' | 'UNKNOWN'
    selected_benefit_ids: string[]
  }>
}
