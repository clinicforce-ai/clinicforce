import { ApiError, json, request } from './client'

export interface IdmeStatus {
  enabled: boolean
  configured: boolean
  linked: boolean
  auth_recent: boolean
}

function assertIdmeStatus(value: unknown): IdmeStatus {
  if (
    !value ||
    typeof value !== 'object' ||
    !('enabled' in value) ||
    typeof value.enabled !== 'boolean' ||
    !('configured' in value) ||
    typeof value.configured !== 'boolean' ||
    !('linked' in value) ||
    typeof value.linked !== 'boolean' ||
    !('auth_recent' in value) ||
    typeof value.auth_recent !== 'boolean'
  ) {
    throw new ApiError(502, 'ClinicForce returned an invalid ID.me status.')
  }
  return value as IdmeStatus
}

export async function getIdmeStatus(): Promise<IdmeStatus> {
  return assertIdmeStatus(await request('/api/patient/idme/status'))
}

export async function beginIdmeAuthorization(
  intent: 'login' | 'link' | 'tefca_ias',
): Promise<string> {
  const value = await request<unknown>('/api/patient/idme/start', json({ intent }))
  if (
    !value ||
    typeof value !== 'object' ||
    !('authorization_url' in value) ||
    typeof value.authorization_url !== 'string'
  ) {
    throw new ApiError(502, 'ClinicForce could not start ID.me.')
  }
  return value.authorization_url
}

export async function disconnectIdme(): Promise<void> {
  await request('/api/patient/idme/link', { method: 'DELETE' })
}
