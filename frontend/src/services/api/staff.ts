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
  source?: 'insurance_card' | 'clinicforce_qr' | 'patient_upload'
  auto_patient_id?: string | null
  auto_patient?: (StaffPatient & { identity_status?: 'unverified' | 'confirmed' }) | null
  expected_patient_id?: string | null
  expected_patient?: StaffPatient | null
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

export interface StaffClinic { id: string; name: string; npi: string | null; upload_url: string | null; can_manage_npi: boolean }
export interface StaffInvitation { id: string; url: string; expires_at: string }
export const getStaffClinic = () => request<StaffClinic>('/api/staff/clinic')
export const registerStaffClinic = (body: { clinic_name: string; npi: string; display_name: string; email: string; password: string }) => request<{ staff: StaffSession }>('/api/staff/register', json(body))
export const setStaffClinicNpi = (npi: string) => request<StaffClinic>('/api/staff/clinic/npi', { ...json({ npi }), method: 'PUT' })
export const getStaffInvitation = (token: string) => request<{ clinic_name: string; expires_at: string }>(`/api/staff/invitations/${encodeURIComponent(token)}`)
export const acceptStaffInvitation = (token: string, body: { display_name: string; email: string; password: string }) => request<{ staff: StaffSession }>(`/api/staff/invitations/${encodeURIComponent(token)}`, json(body))
export const createStaffInvitation = (role: 'staff' | 'admin') => request<StaffInvitation>('/api/staff/invitations', json({ role }))
export const readStaffInsuranceCard = (intakeId: string) => request<StaffIntake>(`/api/staff/intakes/${encodeURIComponent(intakeId)}/read-card`, json({}))
export const getStaffEmailConfiguration = () => request<{ available: boolean; provider: 'ses' | null; reason?: string }>('/api/staff/email/configuration')
export const emailStaffLink = (kind: 'upload-links' | 'invitations', id: string, email: string, url: string) => request<{ status: 'sent' | 'unavailable' | 'failed'; detail: string }>(`/api/staff/${kind}/${encodeURIComponent(id)}/email`, json({ email, url }))
export interface SavedStaffInvitation { id: string; role: 'staff' | 'admin'; status: string; created_at: string; expires_at: string }
export const getStaffInvitations = () => request<{ invitations: SavedStaffInvitation[] }>('/api/staff/invitations')
export const revokeStaffInvitation = (id: string) => request<void>(`/api/staff/invitations/${encodeURIComponent(id)}/revoke`, json({}))

export interface ClinicalPatient {
  id: string
  clinic_patient_id: string | null
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
  intakes: Array<{ id: string; status: string; created_at: string; updated_at: string; source: string }>
}
export interface ClinicalDashboard { patients: ClinicalPatient[]; counts: { all: number; accounts_with_cards: number; accounts_without_cards: number; card_only: number; other: number } }
export const getClinicalPatients = () => request<ClinicalDashboard>('/api/staff/patients/dashboard')
