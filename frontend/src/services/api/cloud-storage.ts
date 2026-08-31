import type { LinkResponse, ManualRecordInput, PatientSession } from './patient-models'
import { ApiError, assertWorkspace, json, request } from './client'
import { credentialsPayload } from './patient'

export async function beginGoogleSignIn(): Promise<string> {
  const value = await request<unknown>('/api/patient/google-login/start', { method: 'POST' })
  if (!value || typeof value !== 'object' || !('authorization_url' in value) || typeof value.authorization_url !== 'string') {
    throw new ApiError(502, 'ClinicForce could not start Google sign-in.')
  }
  return value.authorization_url
}

export async function uploadPatientPdfs(patientId: string | undefined, password: string | undefined, files: File[]): Promise<PatientSession> {
  const form = new FormData()
  if (patientId && password) {
    form.append('patient_id', patientId)
    form.append('password', password)
  }
  files.forEach((file) => form.append('file', file, file.name))
  return assertWorkspace(await request('/api/patient/upload', { method: 'POST', body: form }))
}

export async function chooseStorageProvider(patientId: string | undefined, password: string | undefined, storageProvider: 's3' | 'google_drive'): Promise<PatientSession> {
  return assertWorkspace(await request('/api/patient/storage-preference', json({ ...credentialsPayload(patientId, password), storage_provider: storageProvider })))
}

export async function beginGoogleDriveConnection(patientId?: string, password?: string): Promise<string> {
  const value = await request<unknown>('/api/patient/google-drive/connect', json(credentialsPayload(patientId, password)))
  if (!value || typeof value !== 'object' || !('authorization_url' in value) || typeof value.authorization_url !== 'string') {
    throw new ApiError(502, 'ClinicForce could not start the Google Drive connection.')
  }
  return value.authorization_url
}

export async function savePatientRecord(
  patientId: string | undefined,
  password: string | undefined,
  record: ManualRecordInput,
): Promise<PatientSession> {
  return assertWorkspace(
    await request('/api/patient/records', json({ ...credentialsPayload(patientId, password), ...record })),
  )
}

export async function generatePatientLink(
  patientId: string | undefined,
  password: string | undefined,
  passcode: string | undefined,
  shareMode: 'standard' | 'ecw_compat' = 'standard',
): Promise<LinkResponse> {
  const requestBody = {
    ...credentialsPayload(patientId, password),
    share_mode: shareMode,
    ...(shareMode === 'standard' ? { passcode } : {}),
  }
  const value = await request<unknown>(
    '/api/patient/generate-link',
    json(requestBody),
  )
  if (
    !value ||
    typeof value !== 'object' ||
    !('qr_code_base64' in value) ||
    !('shl_uri' in value) ||
    typeof value.qr_code_base64 !== 'string' ||
    typeof value.shl_uri !== 'string'
  ) {
    throw new ApiError(502, 'The server returned an invalid secure link. Please try again.')
  }
  return value as LinkResponse
}
