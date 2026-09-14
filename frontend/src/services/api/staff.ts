import { ApiError, detailFrom, json, request } from './client'

export interface StaffSession {
  id: string
  username: string
  display_name: string
  practice_id: string
  practice_name: string
  role: 'staff' | 'admin'
}

export interface StaffPatient {
  id: string
  given_name: string
  family_name: string
  birth_date: string
}

export interface StaffPatientFields {
  given_name: string
  family_name: string
  birth_date: string
  member_id: string
  payer: string
  group_number: string
  relationship: string
  subscriber_name: string
  subscriber_birth_date: string
}

export interface StaffIntake {
  id: string
  status: string
  source?: 'insurance_card' | 'clinicforce_qr'
  created_at: string
  updated_at: string
  patient?: StaffPatient | null
  fields: Partial<StaffPatientFields> & { member_name?: string }
  documents: Array<{ id: string; filename: string; content_type: string; size_bytes: number; created_at: string }>
  workflow_documents?: StaffWorkflowDocument[]
  workflows?: { v1?: string; v2?: string }
  events?: Array<{ id: string; action: string; created_at: string; actor?: string }>
  warning?: string | null
  field_sources?: Record<string, string>
}

export interface StaffWorkflowDocument {
  id: string
  version: 'v1' | 'v2'
  kind: string
  filename: string
  content_type: string
  observed_at: string | null
  download_url: string
}

export const getStaffSession = () => request<{ staff: StaffSession }>('/api/staff/session')
export const loginStaff = (username: string, password: string) => request<{ staff: StaffSession }>('/api/staff/login', json({ username, password }))
export const logoutStaff = () => request<void>('/api/staff/logout', json({}))
export const getStaffIntakes = (query = '', status = '', offset = 0) => request<{ intakes: StaffIntake[]; has_more: boolean; next_offset: number | null }>('/api/staff/intakes/search', json({ query, status, offset }))
export const getStaffIntake = (id: string) => request<StaffIntake>(`/api/staff/intakes/${encodeURIComponent(id)}`)
export const findStaffPatients = (fields: Pick<StaffPatientFields, 'given_name' | 'family_name' | 'birth_date'>) => request<{ patients: StaffPatient[] }>('/api/staff/patients/search', json(fields))

export function createStaffIntake(files: File[]) {
  const body = new FormData()
  files.forEach(file => body.append('files', file))
  return request<StaffIntake>('/api/staff/intakes', { method: 'POST', body })
}

export const confirmStaffPatient = (id: string, fields: StaffPatientFields & { identity_confirmed: true; patient_id?: string }) => request<StaffIntake>(`/api/staff/intakes/${encodeURIComponent(id)}/patient`, { ...json(fields), method: 'PUT' })
export const addStaffMember = (member: { username: string; display_name: string; password: string; role: 'staff' | 'admin' }) => request<unknown>('/api/staff/members', json(member))

export interface StaffLibraryDocument {
  id: string
  practice_id: string
  kind: 'contract' | 'fee_schedule' | 'self_pay'
  label: string
  payer: string
  filename: string
  content_type: string
  created_at: string
  effective_from: string | null
  effective_to: string | null
}

export const getStaffLibrary = () => request<{ documents: StaffLibraryDocument[] }>('/api/staff/library')
export const uploadStaffLibrary = (body: FormData) => request<StaffLibraryDocument>('/api/staff/library', { method: 'POST', body })
export const createStaffIntakeFromShare = () => request<StaffIntake>('/api/doctor/insurance/staff-intake', json({}))
export const downloadStaffDocument = (intakeId: string, documentId: string, filename: string) => downloadStaffFile(`/api/staff/intakes/${encodeURIComponent(intakeId)}/documents/${encodeURIComponent(documentId)}`, filename)
export const downloadStaffLibraryDocument = (documentId: string, filename: string) => downloadStaffFile(`/api/staff/library/${encodeURIComponent(documentId)}/document`, filename)
export const downloadStaffWorkflowDocument = (intakeId: string, document: StaffWorkflowDocument) => {
  if (!document.download_url.startsWith(`/api/staff/intakes/${encodeURIComponent(intakeId)}/workflow-documents/`)) return Promise.reject(new Error('This document link is not part of the selected patient intake.'))
  return downloadStaffFile(document.download_url, document.filename)
}

async function downloadStaffFile(path: string, filename: string) {
  const response = await fetch(path, { credentials: 'same-origin', signal: AbortSignal.timeout(45_000) })
  if (!response.ok) throw new ApiError(response.status, detailFrom(await response.json().catch(() => null), 'Unable to download this document.'))
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
}
