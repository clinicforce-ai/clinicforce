import { json, request } from './client'
import { type StaffSession } from './staff-contracts'

export const getStaffSession = () => request<{ staff: StaffSession }>('/api/staff/session')

export const loginStaff = (username: string, password: string) =>
  request<{ staff: StaffSession }>('/api/staff/login', json({ username, password }))

export const logoutStaff = () => request<void>('/api/staff/logout', json({}))
