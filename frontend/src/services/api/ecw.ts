import { assertWorkspace } from './patient-session-contract'
import { type EcwRecord } from './patient-provider-types'
import { type PatientSession } from './patient-session-types'
import { ApiError, json, request } from './client'
import { credentialsPayload } from './patient-credentials'

export async function beginEcwAuthorization(
  patientId: string | undefined,
  password: string | undefined,
  practiceCode: string,
  popup = false,
): Promise<string> {
  const value = await request<unknown>(
    '/api/patient/ecw/authorize',
    json({ ...credentialsPayload(patientId, password), practice_code: practiceCode, popup }),
  )
  if (
    !value ||
    typeof value !== 'object' ||
    !('authorization_url' in value) ||
    typeof value.authorization_url !== 'string'
  ) {
    throw new ApiError(502, 'ClinicForce could not start the eClinicalWorks connection.')
  }
  return value.authorization_url
}

export async function getEcwRecord(): Promise<EcwRecord> {
  const value = await request<unknown>('/api/patient/ecw/record')
  if (
    !value ||
    typeof value !== 'object' ||
    !('bundle' in value) ||
    !('availability' in value) ||
    !('source' in value)
  ) {
    throw new ApiError(502, 'eClinicalWorks returned an invalid clinical-data response.')
  }
  const source = value.source
  if (
    !source ||
    typeof source !== 'object' ||
    !('organization_name' in source) ||
    !('practice_code' in source) ||
    !('retrieved_at' in source)
  ) {
    throw new ApiError(502, 'eClinicalWorks returned invalid source information.')
  }
  return value as EcwRecord
}

export async function importEcwRecord(): Promise<PatientSession> {
  return assertWorkspace(await request('/api/patient/ecw/import', { method: 'POST' }))
}

export async function disconnectEcw(): Promise<void> {
  await request('/api/patient/ecw/live-session', { method: 'DELETE' })
}
