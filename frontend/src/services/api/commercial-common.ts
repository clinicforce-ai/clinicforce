export type CommercialAppointment = { id: string; start: string; status: string; reason: string }

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
  approval_reference?: string
  approval_verified_at?: string
  sources: Array<{
    label?: string
    filename?: string
    page?: number
    document_id?: string
    url?: string
  }>
}

export type CollectionGuidance = {
  billing_note?: BillingNote
  amount_cents: number | null
  range_min_cents?: number | null
  range_max_cents?: number | null
  status: string
  basis: string
  reason: string
  patient_message: string
  assumptions: string[]
  patient_actions: string[]
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
