import type { InsuranceCase, InsuranceStage } from './insurance'
import { request } from './client'

const caseRequest = <T>(path: string, init: RequestInit, staffIntakeId?: string) =>
  request<T>(path, init, { staffIntakeId })

export async function enableInsuranceBeta(): Promise<void> {
  await request('/api/doctor/insurance/beta', { method: 'POST' })
}

export async function createInsuranceCase(
  file: File,
  staffIntakeId?: string,
): Promise<InsuranceCase> {
  const form = new FormData()
  form.append('file', file, file.name)
  return caseRequest<InsuranceCase>(
    '/api/doctor/insurance/cases',
    { method: 'POST', body: form },
    staffIntakeId,
  )
}

export async function confirmInsuranceIntake(
  caseId: string,
  fields: InsuranceCase['ocr'],
  staffIntakeId?: string,
): Promise<InsuranceCase> {
  return caseRequest<InsuranceCase>(
    `/api/doctor/insurance/cases/${encodeURIComponent(caseId)}/intake`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        member_name: fields.member_name,
        member_id: fields.member_id,
        group_number: fields.group_number,
      }),
    },
    staffIntakeId,
  )
}

export async function confirmInsuranceService(
  caseId: string,
  codes: string[],
  quantities: Record<string, number> = {},
  staffIntakeId?: string,
): Promise<InsuranceCase> {
  return caseRequest<InsuranceCase>(
    `/api/doctor/insurance/cases/${encodeURIComponent(caseId)}/service-lines`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ codes, quantities }),
    },
    staffIntakeId,
  )
}

export async function suggestInsuranceServices(
  caseId: string,
  clinicalContext: string,
  visitType?: 'new' | 'established',
  staffIntakeId?: string,
): Promise<InsuranceCase> {
  return caseRequest<InsuranceCase>(
    `/api/doctor/insurance/cases/${encodeURIComponent(caseId)}/clinical-context`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ clinical_context: clinicalContext, visit_type: visitType }),
    },
    staffIntakeId,
  )
}

export async function uploadInsuranceEvidence(
  caseId: string,
  kind: string,
  file: File,
  staffIntakeId?: string,
): Promise<InsuranceCase> {
  const form = new FormData()
  form.append('file', file, file.name)
  return caseRequest<InsuranceCase>(
    `/api/doctor/insurance/cases/${encodeURIComponent(caseId)}/documents/${encodeURIComponent(kind)}`,
    { method: 'POST', body: form },
    staffIntakeId,
  )
}

export async function runInsuranceStage(
  caseId: string,
  stage: Exclude<InsuranceStage, 'intake' | 'service'>,
  staffIntakeId?: string,
): Promise<InsuranceCase> {
  return caseRequest<InsuranceCase>(
    `/api/doctor/insurance/cases/${encodeURIComponent(caseId)}/stages/${encodeURIComponent(stage)}`,
    { method: 'POST' },
    staffIntakeId,
  )
}
