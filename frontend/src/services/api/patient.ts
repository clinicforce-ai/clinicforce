import type { PatientProfile, PatientProfileInput, PatientSession } from './patient-models'
import { ApiError, assertWorkspace, json, request } from './client'

export async function registerPatient(patientId: string, password: string): Promise<PatientSession> {
  return assertWorkspace(await request('/api/patient/register', json({ patient_id: patientId.trim(), password })))
}

export async function loginPatient(patientId: string, password: string): Promise<PatientSession> {
  return assertWorkspace(await request('/api/patient/login', json({ patient_id: patientId.trim(), password })))
}

export function credentialsPayload(patientId?: string, password?: string): Record<string, string> {
  return patientId && password ? { patient_id: patientId.trim(), password } : {}
}

export async function getCurrentPatientSession(): Promise<PatientSession> {
  return assertWorkspace(await request('/api/patient/session'))
}

function assertPatientProfile(value: unknown): PatientProfile {
  if (
    !value || typeof value !== 'object' ||
    !('given_name' in value) || !('family_name' in value) || !('birth_date' in value) ||
    !('administrative_gender' in value) || !('suggested_given_name' in value) || !('suggested_family_name' in value)
  ) {
    throw new ApiError(502, 'The server returned an invalid demographic profile. Please try again.')
  }
  const profile = value as Record<string, unknown>
  const stringOrNull = (field: string) => profile[field] === null || typeof profile[field] === 'string'
  if (!stringOrNull('given_name') || !stringOrNull('family_name') || !stringOrNull('birth_date') || !stringOrNull('suggested_given_name') || !stringOrNull('suggested_family_name')) {
    throw new ApiError(502, 'The server returned an invalid demographic profile. Please try again.')
  }
  if (profile.administrative_gender !== null && !['male', 'female', 'other', 'unknown'].includes(String(profile.administrative_gender))) {
    throw new ApiError(502, 'The server returned an invalid demographic profile. Please try again.')
  }
  return profile as unknown as PatientProfile
}

export async function getPatientProfile(): Promise<PatientProfile> {
  return assertPatientProfile(await request('/api/patient/profile'))
}

export async function savePatientProfile(
  patientId: string | undefined,
  password: string | undefined,
  profile: PatientProfileInput,
): Promise<PatientProfile> {
  return assertPatientProfile(await request('/api/patient/profile', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...credentialsPayload(patientId, password), ...profile }),
  }))
}

export async function logoutPatient(): Promise<void> {
  await request('/api/patient/logout', { method: 'POST' })
}
