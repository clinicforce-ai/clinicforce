import { json, request } from './client'

export interface StaffLibraryDocument {
  id: string
  practice_id: string
  kind: 'contract' | 'fee_schedule' | 'self_pay' | 'era_835' | 'plan_matrix'
  label: string
  payer: string
  filename: string
  content_type: string
  created_at: string
  effective_from: string | null
  effective_to: string | null
  extraction_summary?: {
    status:
      | 'read'
      | 'reading'
      | 'needs_retry'
      | 'usable'
      | 'partial'
      | 'missing_dependency'
      | 'conflict'
      | 'provisional'
    page_count?: number
    empty_pages?: number[]
    usable_rules?: number
    issues?: Array<{ status: string; reason: string; locator?: string }>
  } | null
  estimator_uses?: Array<{
    id: string
    type: 'contract_rates' | 'cash_rates'
    status: string
    payer: string
    effective_from: string
    effective_to?: string | null
    note?: string
  }>
}

export interface MissingRateSummary {
  gaps: Array<{
    payer: string
    code: string
    kind: 'insured_allowed' | 'clinic_cash'
    estimate_count: number
    last_observed_at: string
  }>
  group_count: number
  scanned_case_count: number
  scan_limit: number
  scan_limit_reached: boolean
}

export const getStaffLibrary = () =>
  request<{ documents: StaffLibraryDocument[] }>('/api/staff/library')

export const getStaffMissingRates = () =>
  request<MissingRateSummary>('/api/staff/library/missing-rates')

export const uploadStaffLibrary = (body: FormData) =>
  request<StaffLibraryDocument>('/api/staff/library', { method: 'POST', body })

export const retireStaffRateVersion = (id: string) =>
  request<{ id: string; status: string }>(
    `/api/staff/library/rate-versions/${encodeURIComponent(id)}/retire`,
    json({}),
  )
