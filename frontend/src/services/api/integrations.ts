import type { EcwRecord, EpicEndpoint, EpicRecord, PatientSession } from './patient-models'
import type { InsuranceCase, InsuranceEvidenceInput, InsuranceStage } from './insurance'
import { ApiError, assertWorkspace, detailFrom, json, request } from './client'
import { credentialsPayload } from './patient'

export async function searchEpicEndpoints(query: string): Promise<EpicEndpoint[]> {
  const value = await request<unknown>(`/api/patient/epic/endpoints?q=${encodeURIComponent(query.trim())}`)
  if (!value || typeof value !== 'object' || !('endpoints' in value) || !Array.isArray(value.endpoints)) {
    throw new ApiError(502, 'ClinicForce could not load the Epic hospital directory.')
  }
  return value.endpoints.filter((item): item is EpicEndpoint => Boolean(
    item && typeof item === 'object' && 'id' in item && 'name' in item &&
    typeof item.id === 'string' && typeof item.name === 'string',
  ))
}

export async function beginEpicAuthorization(patientId: string | undefined, password: string | undefined, endpointId: string, popup = false): Promise<string> {
  const value = await request<unknown>('/api/patient/epic/authorize', json({ ...credentialsPayload(patientId, password), endpoint_id: endpointId, popup }))
  if (!value || typeof value !== 'object' || !('authorization_url' in value) || typeof value.authorization_url !== 'string') {
    throw new ApiError(502, 'ClinicForce could not start the Epic connection.')
  }
  return value.authorization_url
}

export async function beginEcwAuthorization(patientId: string | undefined, password: string | undefined, practiceCode: string, popup = false): Promise<string> {
  const value = await request<unknown>('/api/patient/ecw/authorize', json({ ...credentialsPayload(patientId, password), practice_code: practiceCode, popup }))
  if (!value || typeof value !== 'object' || !('authorization_url' in value) || typeof value.authorization_url !== 'string') {
    throw new ApiError(502, 'ClinicForce could not start the eClinicalWorks connection.')
  }
  return value.authorization_url
}

export async function getEpicRecord(): Promise<EpicRecord> {
  const value = await request<unknown>('/api/patient/epic/record')
  if (!value || typeof value !== 'object' || !('bundle' in value) || !('availability' in value) || !('source' in value)) {
    throw new ApiError(502, 'Epic returned an invalid clinical-data response.')
  }
  return value as EpicRecord
}

export async function getEcwRecord(): Promise<EcwRecord> {
  const value = await request<unknown>('/api/patient/ecw/record')
  if (!value || typeof value !== 'object' || !('bundle' in value) || !('availability' in value) || !('source' in value)) {
    throw new ApiError(502, 'eClinicalWorks returned an invalid clinical-data response.')
  }
  const source = value.source
  if (!source || typeof source !== 'object' || !('organization_name' in source) || !('practice_code' in source) || !('retrieved_at' in source)) {
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

export async function enableInsuranceBeta(): Promise<void> {
  await request('/api/doctor/insurance/beta', { method: 'POST' })
}

export async function createInsuranceCase(file: File): Promise<InsuranceCase> {
  const form = new FormData()
  form.append('file', file, file.name)
  return request<InsuranceCase>('/api/doctor/insurance/cases', { method: 'POST', body: form })
}

export async function confirmInsuranceIntake(caseId: string, fields: InsuranceCase['ocr']): Promise<InsuranceCase> {
  return request<InsuranceCase>(`/api/doctor/insurance/cases/${encodeURIComponent(caseId)}/intake`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ member_name: fields.member_name, member_id: fields.member_id, group_number: fields.group_number }),
  })
}

export async function confirmInsuranceService(caseId: string, codes: string[]): Promise<InsuranceCase> {
  return request<InsuranceCase>(`/api/doctor/insurance/cases/${encodeURIComponent(caseId)}/service-lines`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ codes }),
  })
}

export async function uploadInsuranceEvidence(caseId: string, kind: 'eligibility' | 'contract', file: File): Promise<InsuranceCase> {
  const form = new FormData()
  form.append('file', file, file.name)
  return request<InsuranceCase>(`/api/doctor/insurance/cases/${encodeURIComponent(caseId)}/documents/${kind}`, { method: 'POST', body: form })
}

export async function confirmInsuranceEvidence(caseId: string, evidence: InsuranceEvidenceInput): Promise<InsuranceCase> {
  return request<InsuranceCase>(`/api/doctor/insurance/cases/${encodeURIComponent(caseId)}/evidence`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(evidence),
  })
}

export async function runInsuranceStage(caseId: string, stage: Exclude<InsuranceStage, 'intake' | 'service'>): Promise<InsuranceCase> {
  return request<InsuranceCase>(`/api/doctor/insurance/cases/${encodeURIComponent(caseId)}/stages/${encodeURIComponent(stage)}`, { method: 'POST' })
}

export async function disconnectEpic(): Promise<void> {
  await request('/api/patient/epic/live-session', { method: 'DELETE' })
}

async function liveEpicPdf(path: string, init: RequestInit, fallback: string): Promise<Blob> {
  const response = await fetch(path, { ...init, credentials: 'same-origin' })
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null)
    throw new ApiError(response.status, detailFrom(body, fallback))
  }
  return response.blob()
}

export function downloadEpicDocument(documentId: string): Promise<Blob> {
  return liveEpicPdf(`/api/patient/epic/documents/${encodeURIComponent(documentId)}`, { method: 'GET' }, 'Unable to open this Epic document.')
}
