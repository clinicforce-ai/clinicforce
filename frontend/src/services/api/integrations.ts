import type { EcwRecord, EpicEndpoint, EpicRecord, PatientSession } from './patient-models'
import type { InsuranceVerification, InsuranceWorkbenchResult, LocalV2Case, LocalV2Document, LocalV2StageResult, ServiceEstimate, ServiceEstimateInput } from './insurance'
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

export async function verifyDemoInsuranceCard(file: File): Promise<InsuranceVerification> {
  const form = new FormData()
  form.append('file', file, file.name)
  const value = await request<unknown>('/api/clinician/insurance/demo-card', { method: 'POST', body: form })
  if (!value || typeof value !== 'object' || !('demo' in value) || value.demo !== true || !('network' in value) || !('source_conflicts' in value)) {
    throw new ApiError(502, 'The insurance verification demo returned an invalid response.')
  }
  return value as InsuranceVerification
}

export async function estimateDemoService(input: ServiceEstimateInput): Promise<ServiceEstimate> {
  const value = await request<unknown>('/api/clinician/insurance/demo-estimate', json(input))
  if (!value || typeof value !== 'object' || !('demo' in value) || value.demo !== true || !('assistant' in value) || !('gates' in value)) {
    throw new ApiError(502, 'The service estimate demo returned an invalid response.')
  }
  return value as ServiceEstimate
}

export async function runDemoInsuranceStage(input: ServiceEstimateInput & { stage: InsuranceWorkbenchResult['stage'] }): Promise<InsuranceWorkbenchResult> {
  const value = await request<unknown>('/api/clinician/insurance/demo-stage', json(input))
  if (!value || typeof value !== 'object' || !('demo' in value) || value.demo !== true || !('stage' in value) || !('status' in value) || !('decision' in value)) {
    throw new ApiError(502, 'The insurance workflow returned an invalid response.')
  }
  return value as InsuranceWorkbenchResult
}

export async function createLocalV2Case(documents: Record<string, File | null>): Promise<LocalV2Case> {
  const form = new FormData()
  for (const [kind, file] of Object.entries(documents)) if (file) form.append(kind, file, file.name)
  return request<LocalV2Case>('/api/clinician/insurance/local-v2/cases', { method: 'POST', body: form })
}

export async function reviewLocalV2Document(caseId: string, document: LocalV2Document): Promise<LocalV2Case> {
  return request<LocalV2Case>(`/api/clinician/insurance/local-v2/cases/${encodeURIComponent(caseId)}/documents/${encodeURIComponent(document.id)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ fields: document.fields }) })
}

export async function addLocalV2Document(caseId: string, kind: string, file: File): Promise<LocalV2Case> {
  const form = new FormData()
  form.append('kind', kind)
  form.append('file', file, file.name)
  return request<LocalV2Case>(`/api/clinician/insurance/local-v2/cases/${encodeURIComponent(caseId)}/documents`, { method: 'POST', body: form })
}

export async function runLocalV2Stage(caseId: string, stage: string, input: { service: string }): Promise<LocalV2StageResult> {
  return request<LocalV2StageResult>(`/api/clinician/insurance/local-v2/cases/${encodeURIComponent(caseId)}/stages/${encodeURIComponent(stage)}`, json(input))
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
