import { json, request } from './client'
import {
  type StaffPatient,
  type StaffPatientFields,
  type StaffCardDetails,
  type StaffIntake,
} from './staff-contracts'

export const getStaffIntakes = (query = '', status = '', offset = 0) =>
  request<{ intakes: StaffIntake[]; has_more: boolean; next_offset: number | null }>(
    '/api/staff/intakes/search',
    json({ query, status, offset }),
  )

export const getStaffIntake = (id: string) =>
  request<StaffIntake>(`/api/staff/intakes/${encodeURIComponent(id)}`)

export const findStaffPatients = (
  fields: Pick<StaffPatientFields, 'given_name' | 'family_name' | 'birth_date'>,
) => request<{ patients: StaffPatient[] }>('/api/staff/patients/search', json(fields))

export function createStaffIntake(files: File[], details: StaffCardDetails) {
  const body = new FormData()
  files.forEach((file) => body.append('files', file))
  Object.entries(details).forEach(([key, value]) => {
    if (value.trim()) body.append(key, value.trim())
  })
  return request<StaffIntake>('/api/staff/intakes', { method: 'POST', body })
}

export type EcwPatientMatch = {
  patient: {
    ecw_account_number?: string | null
    given_name: string
    family_name: string
    birth_date: string
  }
  appointments: Array<{ id: string; start: string; status: string; reason: string }>
}

export type EcwPatientSuggestion = EcwPatientMatch['patient'] & {
  id: string
  differences: string[]
}

export const createStaffEcwLookup = (details: {
  given_name: string
  middle_initial: string
  family_name: string
  birth_date: string
  ecw_account_number?: string
  selected_patient_id?: string
  identity_confirmed?: boolean
}) =>
  request<
    | (StaffIntake & { ecw_match: EcwPatientMatch })
    | { status: 'selection_required'; candidates: EcwPatientSuggestion[] }
  >('/api/staff/intakes/ecw-lookup', json(details))

export const confirmStaffPatient = (
  id: string,
  fields: StaffPatientFields & { identity_confirmed: true; patient_id?: string },
) =>
  request<StaffIntake>(`/api/staff/intakes/${encodeURIComponent(id)}/patient`, {
    ...json(fields),
    method: 'PUT',
  })

export const readStaffInsuranceCard = (intakeId: string) =>
  request<StaffIntake>(`/api/staff/intakes/${encodeURIComponent(intakeId)}/read-card`, json({}))

export const deleteStaffIntake = (id: string) =>
  request<{ deleted: boolean; cleanup_pending: boolean }>(
    `/api/staff/intakes/${encodeURIComponent(id)}`,
    { method: 'DELETE' },
  )
