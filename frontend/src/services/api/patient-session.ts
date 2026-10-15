import type { PatientSession } from './patient-session-types'
import { assertWorkspace } from './patient-session-contract'
import { json, request } from './client'

export async function registerPatient(
  patientId: string,
  password: string,
): Promise<PatientSession> {
  return assertWorkspace(
    await request('/api/patient/register', json({ patient_id: patientId.trim(), password })),
  )
}

export async function loginPatient(patientId: string, password: string): Promise<PatientSession> {
  return assertWorkspace(
    await request('/api/patient/login', json({ patient_id: patientId.trim(), password })),
  )
}

export async function getCurrentPatientSession(): Promise<PatientSession> {
  return assertWorkspace(await request('/api/patient/session'))
}

export async function logoutPatient(): Promise<void> {
  await request('/api/patient/logout', { method: 'POST' })
}
