import { json, request } from './client'

export interface UploadClinic { npi: string; name: string }
export interface PatientUploadLink { id?: string; upload_code: string; url: string; clinic: UploadClinic; expires_at: string }
export interface UploadReceipt { receipt_id: string; status: 'received'; received_at: string; clinic_name: string }
export interface SavedUploadLink { id: string; npi: string; patient_id: string | null; status: string; created_at: string; expires_at: string; submitted_at: string | null; intake_id: string | null }
export const getUploadClinic = (npi: string) => request<{ clinic: UploadClinic }>(`/api/insurance-upload/clinics/${encodeURIComponent(npi)}`)
export const startPatientUpload = (npi: string) => request<PatientUploadLink>('/api/insurance-upload/start', json({ npi }))
export const getPatientUpload = (code: string, token: string) => request<{ clinic_name: string; expires_at: string; status: 'pending' | 'received' }>(`/api/insurance-upload/${encodeURIComponent(code)}/${encodeURIComponent(token)}`)
export const submitPatientUpload = (code: string, token: string, body: FormData) => request<UploadReceipt>(`/api/insurance-upload/${encodeURIComponent(code)}/${encodeURIComponent(token)}`, { method: 'POST', body })
export const createPatientUploadLink = (patient_id?: string, intake_id?: string) => request<PatientUploadLink & { id: string }>('/api/staff/upload-links', json(patient_id ? { patient_id } : intake_id ? { intake_id } : {}))
export const getPatientUploadLinks = () => request<{ upload_links: SavedUploadLink[] }>('/api/staff/upload-links')
export const revokePatientUploadLink = (id: string) => request<{ id: string; status: 'revoked' }>(`/api/staff/upload-links/${encodeURIComponent(id)}/revoke`, json({}))

export function readInsuranceUploadLocation(value: string): { npi: string; token: string } | null {
  try {
    const url = new URL(value, window.location.origin)
    if (url.origin !== window.location.origin) return null
    const parts = url.pathname.split('/').filter(Boolean)
    const npi = url.searchParams.get('clinic') || (parts.length <= 2 ? parts[0] : '') || ''
    const token = url.searchParams.get('upload') || (parts.length === 2 ? parts[1] : '') || ''
    // Existing private capability links can finish during their short lifetime.
    if (!/^(?:\d{10}|[a-z][a-z0-9-]{2,39})$/.test(npi)) return null
    return { npi, token }
  } catch { return null }
}

export const getInsuranceUploadAccount = () => request<{ available: boolean; given_name?: string; family_name?: string; birth_date?: string }>('/api/insurance-upload/account')
