export type RecordCategory =
  | 'hospital'
  | 'lab'
  | 'vital'
  | 'condition'
  | 'allergy'
  | 'medication'

export interface PatientDocument {
  id: number
  filename: string
  uploaded_at: string
  resource_count: number
}

export interface PatientRecord {
  id: number
  category: RecordCategory
  label: string
  created_at: string
}

export interface PatientSession {
  patient_id: string
  documents: PatientDocument[]
  records: PatientRecord[]
}

export interface LinkResponse {
  shl_uri: string
  qr_code_base64: string
}

export interface ManualRecordInput {
  category: RecordCategory
  hospital?: string
  name?: string
  value?: string
  unit?: string
  date?: string
  details?: string
  dosage?: string
}

export interface ClinicalMeasure {
  name: string
  value: string | number
  unit?: string | null
  date?: string | null
  details?: string | null
  citation?: SourceCitation | null
}

export interface SourceCitation {
  source_filename: string
  page_number: number
  document_id?: number
  download_url?: string
}

export interface Medication {
  name: string
  dosage: string
  details?: string | null
  citation?: SourceCitation | null
}

export interface ClinicalItem {
  name: string
  details?: string | null
  citation?: SourceCitation | null
}

export interface CareEvent {
  facility: string
  date?: string | null
  details?: string | null
  citation?: SourceCitation | null
}

export interface DoctorSummary {
  summary: {
    narrative: string
    vitals: ClinicalMeasure[]
    labs: ClinicalMeasure[]
    medications: Medication[]
    conditions: ClinicalItem[]
    allergies: ClinicalItem[]
    care_events: CareEvent[]
  }
  pdf_url: string
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

const REQUEST_TIMEOUT_MS = 45_000

function detailFrom(body: unknown, fallback: string): string {
  if (!body || typeof body !== 'object' || !('detail' in body)) return fallback
  if (typeof body.detail === 'string') return body.detail
  if (Array.isArray(body.detail)) {
    const messages = body.detail
      .map((item) =>
        item && typeof item === 'object' && 'msg' in item && typeof item.msg === 'string'
          ? item.msg.replace(/^Value error, /, '')
          : null,
      )
      .filter((message): message is string => Boolean(message))
    if (messages.length) return messages.join(' ')
  }
  return fallback
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const response = await fetch(path, { ...init, signal: controller.signal })
    const body: unknown = await response.json().catch(() => null)
    if (!response.ok) {
      throw new ApiError(response.status, detailFrom(body, `Request failed (${response.status}).`))
    }
    return body as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(408, 'The request took too long. Check your connection and try again.')
    }
    throw new ApiError(0, 'Unable to reach ClinicForce. Check your connection and try again.')
  } finally {
    window.clearTimeout(timeout)
  }
}

function json(body: object): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

function assertWorkspace(value: unknown): PatientSession {
  if (
    !value ||
    typeof value !== 'object' ||
    !('patient_id' in value) ||
    !('documents' in value) ||
    !('records' in value) ||
    typeof value.patient_id !== 'string' ||
    !Array.isArray(value.documents) ||
    !Array.isArray(value.records)
  ) {
    throw new ApiError(502, 'The server returned an invalid patient workspace. Please try again.')
  }
  return value as PatientSession
}

export async function registerPatient(patientId: string, password: string): Promise<PatientSession> {
  return assertWorkspace(await request('/api/patient/register', json({ patient_id: patientId.trim(), password })))
}

export async function loginPatient(patientId: string, password: string): Promise<PatientSession> {
  return assertWorkspace(await request('/api/patient/login', json({ patient_id: patientId.trim(), password })))
}

export async function uploadPatientPdfs(patientId: string, password: string, files: File[]): Promise<PatientSession> {
  const form = new FormData()
  form.append('patient_id', patientId)
  form.append('password', password)
  files.forEach((file) => form.append('file', file, file.name))
  return assertWorkspace(await request('/api/patient/upload', { method: 'POST', body: form }))
}

export async function savePatientRecord(
  patientId: string,
  password: string,
  record: ManualRecordInput,
): Promise<PatientSession> {
  return assertWorkspace(
    await request('/api/patient/records', json({ patient_id: patientId, password, ...record })),
  )
}

export async function generatePatientLink(
  patientId: string,
  password: string,
  passcode: string,
): Promise<LinkResponse> {
  const value = await request<unknown>(
    '/api/patient/generate-link',
    json({ patient_id: patientId, password, passcode }),
  )
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

export async function downloadPatientDocument(
  patientId: string,
  password: string,
  documentId: number,
): Promise<Blob> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const response = await fetch(`/api/patient/documents/${documentId}/download`, {
      ...json({ patient_id: patientId, password }),
      signal: controller.signal,
    })
    if (!response.ok) {
      const body: unknown = await response.json().catch(() => null)
      throw new ApiError(response.status, detailFrom(body, 'Unable to open this report.'))
    }
    return response.blob()
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(408, 'The report took too long to open. Please try again.')
    }
    throw new ApiError(0, 'Unable to reach ClinicForce. Check your connection and try again.')
  } finally {
    window.clearTimeout(timeout)
  }
}

export async function fetchManifest(manifestUrl: string, pin: string): Promise<string> {
  const manifest = await request<unknown>(manifestUrl, json({ passcode: pin.trim() }))
  const jwe =
    manifest &&
    typeof manifest === 'object' &&
    'files' in manifest &&
    Array.isArray(manifest.files)
      ? manifest.files[0]?.embedded
      : undefined
  if (typeof jwe !== 'string') {
    throw new ApiError(502, 'The patient link did not contain an encrypted clinical file.')
  }
  return jwe
}

export async function decryptAndSummarize(jwe: string, key: string): Promise<DoctorSummary> {
  return request<DoctorSummary>('/api/doctor/decrypt-and-summarize', json({ jwe_payload: jwe, key }))
}
