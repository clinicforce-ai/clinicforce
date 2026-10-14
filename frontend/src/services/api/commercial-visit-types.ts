import type { CommercialAppointment } from './commercial-common'
import type { PrevisitEstimate } from './commercial-previsit'

export type CommercialPastVisit = CommercialAppointment & {
  date: string
  visit_type: string
  coded_services: string[]
  saved_estimate: {
    created_at?: string
    allowed_cents?: number | null
    patient_cents?: number | null
    plan_cents?: number | null
    disclaimer?: string | null
  } | null
}

export type VisitCode = {
  id?: string
  code: string
  source: string
  description?: string
  units?: number | string | null
  modifiers?: string[]
  unit_allowed_cents?: number | null
  unit_cash_cents?: number | null
  allowed_basis?: 'clinic_contract' | 'payer_out_of_network'
  rate_source?: string
}

type VisitRequirement = {
  required: string
  status: string
  cleared: boolean
  reference?: string
  verified_at?: string
  verified_by?: string
  valid_to?: string
}

export type VisitPricedLine = VisitCode & {
  id: string
  allowed_cents?: number | null
  unit_allowed_cents?: number | null
  allowed_basis?: 'clinic_contract' | 'payer_out_of_network'
  cash_cents?: number | null
  cash_source?: string
  cash_rule?: { locator?: string; document_id?: string }
  cash_included?: boolean
  benefit_verification?: {
    reference: string
    notes: string
    verified_by: string
    verified_at: string
    valid_from: string
    valid_to: string
  } | null
  rate_source: string
  rate_version?: string
  rate_row_number?: number | null
  rate_effective_from?: string
  rate_resolution?: {
    status: 'RESOLVED' | 'RATE_UNAVAILABLE' | 'RATE_CONFLICT'
    matched_candidate_count: number
    source_checked: string
    provider_network_verified: boolean
  }
  benefit_source?: string | null
  benefit_basis: string
  issues: string[]
  conditions?: string[]
  benefits: NonNullable<PrevisitEstimate['benefits']> & {
    copay_period?: string
    benefit_label?: string
    benefit_summary?: string
    visits_remaining?: number
    deductible_applies?: boolean
    copay_id?: string | null
    coinsurance_id?: string | null
    deductible_id?: string | null
    deductible_annual_id?: string | null
    oop_id?: string | null
    deductible_applicability_ids?: string[]
    policy_evidence?: { title: string; url: string; section: string }
    requirements: { referral: VisitRequirement; authorization: VisitRequirement }
  }
  calculation: null | {
    cost_share?: string
    allowed_cents: number
    patient_cents: number
    plan_cents: number
    deductible_cents: number
    copay_cents: number
    coinsurance_cents: number
    oop_adjustment_cents: number
  }
}

export type VisitReview = {
  emg_required?: boolean
  identity_confirmation?: { binding: string; reference: string; notes: string }
  product_mapping?: { reference: string; notes: string; valid_from: string; valid_to: string }
  payment_mode?: 'cash' | 'insurance'
  cash_selection?: string
  selected_ids?: string[]
  confirmed_units?: Record<string, number>
  payments_received_cents?: number
  payment_reference?: string
  verification?: {
    line_id: string
    kind: 'referral' | 'authorization' | 'benefits'
    status: string
    reference: string
    notes: string
    valid_from: string
    valid_to: string
    units: number
    selected_ids?: string[]
    cost_share?: string
    deductible_applies?: boolean
  }
}

export type VisitFinancials = {
  source: string
  checked_at: string
  status: string
  partial: boolean
  message: string
  transactions_checked: number
  lines: Array<{
    transaction_id: string
    claim_reference?: string
    line_index: number
    claim_status?: string
    code: string
    modifiers: string[]
    service_date?: string
    payer?: string
    allowed_cents: number | null
    insurer_paid_cents: number | null
    patient_responsibility_cents: number | null
    source: string
    received_at: string
  }>
}
