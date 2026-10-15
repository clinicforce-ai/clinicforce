import { ApiError, json, request } from './client'
import { credentialsPayload } from './patient-credentials'

export async function beginGoogleSignIn(): Promise<string> {
  const value = await request<unknown>('/api/patient/google-login/start', { method: 'POST' })
  if (
    !value ||
    typeof value !== 'object' ||
    !('authorization_url' in value) ||
    typeof value.authorization_url !== 'string'
  ) {
    throw new ApiError(502, 'ClinicForce could not start Google sign-in.')
  }
  return value.authorization_url
}

export async function beginGoogleDriveConnection(
  patientId?: string,
  password?: string,
): Promise<string> {
  const value = await request<unknown>(
    '/api/patient/google-drive/connect',
    json(credentialsPayload(patientId, password)),
  )
  if (
    !value ||
    typeof value !== 'object' ||
    !('authorization_url' in value) ||
    typeof value.authorization_url !== 'string'
  ) {
    throw new ApiError(502, 'ClinicForce could not start the Google Drive connection.')
  }
  return value.authorization_url
}
