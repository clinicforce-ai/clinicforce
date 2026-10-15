import { type LinkResponse } from './patient-link-types'
import { ApiError, json, request } from './client'
import { credentialsPayload } from './patient-credentials'

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
  const value = await request<unknown>('/api/patient/generate-link', json(requestBody))
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
