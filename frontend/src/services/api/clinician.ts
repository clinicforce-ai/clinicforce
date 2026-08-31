import type { DoctorSummary } from './clinical-models'
import { json, request } from './client'

export async function decryptAndSummarize(manifestId: string, key: string, passcode: string): Promise<DoctorSummary> {
  return request<DoctorSummary>('/api/doctor/decrypt-and-summarize', json({ manifest_id: manifestId, key, passcode }))
}
