import { assertWorkspace } from './patient-session-contract'
import { type ManualRecordInput } from './patient-document-types'
import { type PatientSession } from './patient-session-types'
import { json, request } from './client'
import { credentialsPayload } from './patient-credentials'

export async function savePatientRecord(
  patientId: string | undefined,
  password: string | undefined,
  record: ManualRecordInput,
): Promise<PatientSession> {
  return assertWorkspace(
    await request(
      '/api/patient/records',
      json({ ...credentialsPayload(patientId, password), ...record }),
    ),
  )
}
