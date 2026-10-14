import { assertWorkspace } from './patient-session-contract'
import { type PatientSession } from './patient-session-types'
import { json, request } from './client'
import { credentialsPayload } from './patient-credentials'

export async function uploadPatientPdfs(
  patientId: string | undefined,
  password: string | undefined,
  files: File[],
): Promise<PatientSession> {
  const form = new FormData()
  if (patientId && password) {
    form.append('patient_id', patientId)
    form.append('password', password)
  }
  files.forEach((file) => form.append('file', file, file.name))
  return assertWorkspace(await request('/api/patient/upload', { method: 'POST', body: form }))
}

export async function chooseStorageProvider(
  patientId: string | undefined,
  password: string | undefined,
  storageProvider: 's3' | 'google_drive',
): Promise<PatientSession> {
  return assertWorkspace(
    await request(
      '/api/patient/storage-preference',
      json({ ...credentialsPayload(patientId, password), storage_provider: storageProvider }),
    ),
  )
}
