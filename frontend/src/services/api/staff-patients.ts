import { request } from './client'
import { type SavedInsuranceCheck } from './staff-history'

export interface ClinicalPatient {
  id: string
  clinic_patient_id: string | null
  ecw_account_number?: string | null
  ecw_linked: boolean
  ecw_visit_cache?: Array<{ date?: string; start?: string | null; status: string }> | null
  ecw_visit_cached_at?: string | null
  identity_status: 'unverified' | 'confirmed'
  given_name: string
  family_name: string
  birth_date: string
  email: string | null
  account_status: 'portal' | 'automatic' | 'clinic'
  card_status: 'received' | 'missing'
  latest_intake_id: string | null
  intake_ids: string[]
  intake_count: number
  updated_at: string
  intakes: Array<{
    id: string
    status: string
    created_at: string
    updated_at: string
    source: string
  }>
}

export interface ClinicalDashboard {
  pending_file_cleanup?: number
  patients: ClinicalPatient[]
  counts: {
    all: number
    accounts_with_cards: number
    accounts_without_cards: number
    card_only: number
    other: number
  }
}

export const getClinicalPatients = () => request<ClinicalDashboard>('/api/staff/patients/dashboard')

export type DateAppointments = {
  date: string
  visits: Array<{ clinic_patient_id: string; date: string; start: string; status: string }>
}

export const getTodayAppointments = () =>
  request<DateAppointments>('/api/staff/patients/today-appointments')

export const getAppointmentDate = (day: string) =>
  request<DateAppointments>(`/api/staff/patients/appointment-date?day=${encodeURIComponent(day)}`)

type PatientAppointment = {
  id: string
  appointment_id?: string
  start: string | null
  date?: string
  status: string
  reason: string | null
  visit_type?: string
  from_ecw: boolean
  checks: SavedInsuranceCheck[]
}

export type PatientAppointmentHistory = {
  appointments: PatientAppointment[]
  ecw_status: 'not_requested' | 'not_linked' | 'complete' | 'incomplete'
  ecw_account_number?: string | null
}

export const getPatientAppointments = (patientId: string, includeEcw = false) =>
  request<PatientAppointmentHistory>(
    `/api/staff/patients/${encodeURIComponent(patientId)}/appointments?include_ecw=${includeEcw}`,
  )

export type DeletionResult = { deleted: boolean; cleanup_pending: boolean }

export const deleteStaffPatient = (id: string) =>
  request<DeletionResult>(`/api/staff/patients/${encodeURIComponent(id)}`, { method: 'DELETE' })

export const retryStaffDeletionCleanup = () =>
  request<{ pending_file_cleanup: number }>('/api/staff/deletions/retry-cleanup', {
    method: 'POST',
  })
