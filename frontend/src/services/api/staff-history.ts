import { request } from './client'
import { type StaffPatient } from './staff-contracts'
import { type DeletionResult } from './staff-patients'

export const deleteStaffInsuranceCheck = (intakeId: string, caseId: string) =>
  request<DeletionResult>(
    `/api/staff/intakes/${encodeURIComponent(intakeId)}/checks/${encodeURIComponent(caseId)}`,
    { method: 'DELETE' },
  )

export type SavedInsuranceCheck = {
  id: string
  created_at: string
  label: string
  pending: boolean
  intake_id?: string
  source_mode?: string
  service_date?: string
  visit_type?: string
}

export type InsuranceCheckSnapshot = {
  result: import('./commercial-estimator').PrevisitEstimate
  patient: StaffPatient
  saved_at: string | null
  stage: string
  legacy?: boolean
}

export const getStaffInsuranceCheck = (intakeId: string, checkId: string) =>
  request<{ snapshots: InsuranceCheckSnapshot[] }>(
    `/api/staff/intakes/${encodeURIComponent(intakeId)}/checks/${encodeURIComponent(checkId)}`,
  )

export const getStaffCheckFinancials = (intakeId: string, checkId: string) =>
  request<import('./commercial-estimator').VisitFinancials>(
    `/api/staff/intakes/${encodeURIComponent(intakeId)}/checks/${encodeURIComponent(checkId)}/financials`,
  )

export const getStaffInsuranceChecks = (intakeId: string) =>
  request<{ checks: SavedInsuranceCheck[] }>(
    `/api/staff/intakes/${encodeURIComponent(intakeId)}/checks`,
  )
