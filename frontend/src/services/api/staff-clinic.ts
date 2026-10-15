import { json, request } from './client'
import { type StaffSession } from './staff-contracts'

export const addStaffMember = (member: {
  username: string
  display_name: string
  password: string
  role: 'staff' | 'admin'
}) => request<unknown>('/api/staff/members', json(member))

export interface StaffClinic {
  id: string
  name: string
  npi: string | null
  upload_url: string | null
  can_manage_npi: boolean
}

export interface StaffInvitation {
  id: string
  url: string
  expires_at: string
}

export const getStaffClinic = () => request<StaffClinic>('/api/staff/clinic')

export const registerStaffClinic = (body: {
  clinic_name: string
  npi: string
  display_name: string
  email: string
  password: string
}) => request<{ staff: StaffSession }>('/api/staff/register', json(body))

export const setStaffClinicNpi = (npi: string) =>
  request<StaffClinic>('/api/staff/clinic/npi', { ...json({ npi }), method: 'PUT' })

export const getStaffInvitation = (token: string) =>
  request<{ clinic_name: string; expires_at: string }>(
    `/api/staff/invitations/${encodeURIComponent(token)}`,
  )

export const acceptStaffInvitation = (
  token: string,
  body: { display_name: string; email: string; password: string },
) =>
  request<{ staff: StaffSession }>(
    `/api/staff/invitations/${encodeURIComponent(token)}`,
    json(body),
  )

export const createStaffInvitation = (role: 'staff' | 'admin') =>
  request<StaffInvitation>('/api/staff/invitations', json({ role }))

export const getStaffEmailConfiguration = () =>
  request<{ available: boolean; provider: 'ses' | null; reason?: string }>(
    '/api/staff/email/configuration',
  )

export const emailStaffLink = (
  kind: 'upload-links' | 'invitations',
  id: string,
  email: string,
  url: string,
) =>
  request<{ status: 'sent' | 'unavailable' | 'failed'; detail: string }>(
    `/api/staff/${kind}/${encodeURIComponent(id)}/email`,
    json({ email, url }),
  )

export interface SavedStaffInvitation {
  id: string
  role: 'staff' | 'admin'
  status: string
  created_at: string
  expires_at: string
}

export const getStaffInvitations = () =>
  request<{ invitations: SavedStaffInvitation[] }>('/api/staff/invitations')

export const revokeStaffInvitation = (id: string) =>
  request<void>(`/api/staff/invitations/${encodeURIComponent(id)}/revoke`, json({}))
