import { json, request } from './client'
import type {
  V2Resource,
  V2Document,
  V2Configuration,
  V2Appointment,
  V2ManualReview,
  V2Case,
} from './insurance-v2-types'
export type * from './insurance-v2-types'

const base = '/api/doctor/insurance/v2'
const v2Request = <T>(path: string, init: RequestInit, staffIntakeId?: string) =>
  request<T>(path, init, { staffIntakeId })

export const getV2Configuration = (staffIntakeId?: string) =>
  v2Request<V2Configuration>(`${base}/configuration`, {}, staffIntakeId)
export const latestV2Case = (staffIntakeId?: string) =>
  v2Request<{ case: V2Case | null }>(`${base}/cases/latest`, {}, staffIntakeId)
export const getV2Appointments = (staffIntakeId?: string) =>
  v2Request<{ appointments: V2Appointment[] }>(`${base}/appointments`, {}, staffIntakeId)
export const createV2Case = (
  appointment_id?: string,
  scenario = 'Coverages-Complete-i',
  staffIntakeId?: string,
) =>
  v2Request<V2Case>(
    `${base}/cases`,
    json({ scenario, ...(appointment_id ? { appointment_id } : {}) }),
    staffIntakeId,
  )
export const getV2Case = (id: string, staffIntakeId?: string) =>
  v2Request<V2Case>(`${base}/cases/${encodeURIComponent(id)}`, {}, staffIntakeId)
export const saveV2ManualReview = (id: string, review: V2ManualReview, staffIntakeId?: string) =>
  v2Request<V2Case>(
    `${base}/cases/${encodeURIComponent(id)}/manual-review`,
    {
      ...json(review),
      method: 'PUT',
    },
    staffIntakeId,
  )
export const getV2Requirements = (id: string, staffIntakeId?: string) =>
  v2Request<V2Case>(`${base}/cases/${encodeURIComponent(id)}/requirements`, json({}), staffIntakeId)
export const suggestV2Services = (id: string, staffIntakeId?: string) =>
  v2Request<V2Case>(`${base}/cases/${encodeURIComponent(id)}/suggestions`, json({}), staffIntakeId)
export const confirmV2Services = (
  id: string,
  lines: Array<{ code: string; quantity: number; service_status: 'scheduled' | 'possible' }>,
  staffIntakeId?: string,
) =>
  v2Request<V2Case>(
    `${base}/cases/${encodeURIComponent(id)}/services`,
    json({ lines }),
    staffIntakeId,
  )
export const submitV2Transaction = (id: string, resource: V2Resource, staffIntakeId?: string) =>
  v2Request<V2Case>(
    `${base}/cases/${encodeURIComponent(id)}/transactions/${resource}`,
    json({}),
    staffIntakeId,
  )
export const refreshV2Transaction = (id: string, resource: V2Resource, staffIntakeId?: string) =>
  v2Request<V2Case>(
    `${base}/cases/${encodeURIComponent(id)}/transactions/${resource}/refresh`,
    json({}),
    staffIntakeId,
  )
export const retryV2Transaction = (id: string, resource: V2Resource, staffIntakeId?: string) =>
  v2Request<V2Case>(
    `${base}/cases/${encodeURIComponent(id)}/transactions/${resource}/retry`,
    json({}),
    staffIntakeId,
  )
export function uploadV2Document(id: string, kind: V2Document, file: File, staffIntakeId?: string) {
  const body = new FormData()
  body.append('file', file)
  return v2Request<V2Case>(
    `${base}/cases/${encodeURIComponent(id)}/documents/${kind}`,
    {
      method: 'POST',
      body,
    },
    staffIntakeId,
  )
}
