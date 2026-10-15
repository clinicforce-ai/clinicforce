import { type EpicEndpoint, type EpicRecord } from './patient-provider-types'
import { ApiError, detailFrom, json, request } from './client'
import { credentialsPayload } from './patient-credentials'

export async function searchEpicEndpoints(query: string): Promise<EpicEndpoint[]> {
  const value = await request<unknown>(
    `/api/patient/epic/endpoints?q=${encodeURIComponent(query.trim())}`,
  )
  if (
    !value ||
    typeof value !== 'object' ||
    !('endpoints' in value) ||
    !Array.isArray(value.endpoints)
  ) {
    throw new ApiError(502, 'ClinicForce could not load the Epic hospital directory.')
  }
  return value.endpoints.filter((item): item is EpicEndpoint =>
    Boolean(
      item &&
      typeof item === 'object' &&
      'id' in item &&
      'name' in item &&
      typeof item.id === 'string' &&
      typeof item.name === 'string',
    ),
  )
}

export async function beginEpicAuthorization(
  patientId: string | undefined,
  password: string | undefined,
  endpointId: string,
  popup = false,
): Promise<string> {
  const value = await request<unknown>(
    '/api/patient/epic/authorize',
    json({ ...credentialsPayload(patientId, password), endpoint_id: endpointId, popup }),
  )
  if (
    !value ||
    typeof value !== 'object' ||
    !('authorization_url' in value) ||
    typeof value.authorization_url !== 'string'
  ) {
    throw new ApiError(502, 'ClinicForce could not start the Epic connection.')
  }
  return value.authorization_url
}

export async function getEpicRecord(): Promise<EpicRecord> {
  const value = await request<unknown>('/api/patient/epic/record')
  if (
    !value ||
    typeof value !== 'object' ||
    !('bundle' in value) ||
    !('availability' in value) ||
    !('source' in value)
  ) {
    throw new ApiError(502, 'Epic returned an invalid clinical-data response.')
  }
  return value as EpicRecord
}

export async function disconnectEpic(): Promise<void> {
  await request('/api/patient/epic/live-session', { method: 'DELETE' })
}

async function liveEpicPdf(path: string, init: RequestInit, fallback: string): Promise<Blob> {
  const response = await fetch(path, { ...init, credentials: 'same-origin' })
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null)
    throw new ApiError(response.status, detailFrom(body, fallback))
  }
  return response.blob()
}

export function downloadEpicDocument(documentId: string): Promise<Blob> {
  return liveEpicPdf(
    `/api/patient/epic/documents/${encodeURIComponent(documentId)}`,
    { method: 'GET' },
    'Unable to open this Epic document.',
  )
}
