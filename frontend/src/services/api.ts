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
  storage_provider: 's3' | 'google_drive'
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
  storage_provider: 's3' | 'google_drive'
  drive_connected: boolean
  drive_pending_import_count: number
  drive_sync_message: string | null
  drive_access_upgrade_required: boolean
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
    const response = await fetch(path, { ...init, credentials: 'same-origin', signal: controller.signal })
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
    !('storage_provider' in value) ||
    !('drive_connected' in value) ||
    !('drive_pending_import_count' in value) ||
    !('drive_sync_message' in value) ||
    !('drive_access_upgrade_required' in value) ||
    typeof value.patient_id !== 'string' ||
    !Array.isArray(value.documents) ||
    !Array.isArray(value.records) ||
    (value.storage_provider !== 's3' && value.storage_provider !== 'google_drive') ||
    typeof value.drive_connected !== 'boolean' ||
    typeof value.drive_pending_import_count !== 'number' ||
    (value.drive_sync_message !== null && typeof value.drive_sync_message !== 'string') ||
    typeof value.drive_access_upgrade_required !== 'boolean'
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

function credentialsPayload(patientId?: string, password?: string): Record<string, string> {
  return patientId && password ? { patient_id: patientId.trim(), password } : {}
}

export async function getCurrentPatientSession(): Promise<PatientSession> {
  return assertWorkspace(await request('/api/patient/session'))
}

export async function logoutPatient(): Promise<void> {
  await request('/api/patient/logout', { method: 'POST' })
}

export async function beginGoogleSignIn(): Promise<string> {
  const value = await request<unknown>('/api/patient/google-login/start', { method: 'POST' })
  if (!value || typeof value !== 'object' || !('authorization_url' in value) || typeof value.authorization_url !== 'string') {
    throw new ApiError(502, 'ClinicForce could not start Google sign-in.')
  }
  return value.authorization_url
}

export async function uploadPatientPdfs(patientId: string | undefined, password: string | undefined, files: File[]): Promise<PatientSession> {
  const form = new FormData()
  if (patientId && password) {
    form.append('patient_id', patientId)
    form.append('password', password)
  }
  files.forEach((file) => form.append('file', file, file.name))
  return assertWorkspace(await request('/api/patient/upload', { method: 'POST', body: form }))
}

export async function chooseStorageProvider(patientId: string | undefined, password: string | undefined, storageProvider: 's3' | 'google_drive'): Promise<PatientSession> {
  return assertWorkspace(await request('/api/patient/storage-preference', json({ ...credentialsPayload(patientId, password), storage_provider: storageProvider })))
}

export async function beginGoogleDriveConnection(patientId?: string, password?: string): Promise<string> {
  const value = await request<unknown>('/api/patient/google-drive/connect', json(credentialsPayload(patientId, password)))
  if (!value || typeof value !== 'object' || !('authorization_url' in value) || typeof value.authorization_url !== 'string') {
    throw new ApiError(502, 'ClinicForce could not start the Google Drive connection.')
  }
  return value.authorization_url
}

export async function savePatientRecord(
  patientId: string | undefined,
  password: string | undefined,
  record: ManualRecordInput,
): Promise<PatientSession> {
  return assertWorkspace(
    await request('/api/patient/records', json({ ...credentialsPayload(patientId, password), ...record })),
  )
}

export async function generatePatientLink(
  patientId: string | undefined,
  password: string | undefined,
  passcode: string,
): Promise<LinkResponse> {
  const value = await request<unknown>(
    '/api/patient/generate-link',
    json({ ...credentialsPayload(patientId, password), passcode }),
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
  patientId: string | undefined,
  password: string | undefined,
  documentId: number,
): Promise<Blob> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const response = await fetch(`/api/patient/documents/${documentId}/download`, {
      ...json(credentialsPayload(patientId, password)),
      credentials: 'same-origin',
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

export async function downloadPatientDocumentsArchive(
  patientId: string | undefined,
  password: string | undefined,
  documentIds: number[],
): Promise<Blob> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const response = await fetch('/api/patient/documents/bulk-download', {
      ...json({ ...credentialsPayload(patientId, password), document_ids: documentIds }),
      credentials: 'same-origin',
      signal: controller.signal,
    })
    if (!response.ok) {
      const body: unknown = await response.json().catch(() => null)
      throw new ApiError(response.status, detailFrom(body, 'Unable to download the selected reports.'))
    }
    return response.blob()
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError(408, 'The selected reports took too long to download. Please try again.')
    }
    throw new ApiError(0, 'Unable to reach ClinicForce. Check your connection and try again.')
  } finally {
    window.clearTimeout(timeout)
  }
}

export async function importGoogleDriveChanges(patientId: string | undefined, password: string | undefined): Promise<PatientSession> {
  return assertWorkspace(await request('/api/patient/google-drive/import', json(credentialsPayload(patientId, password))))
}

export async function deletePatientDocument(patientId: string | undefined, password: string | undefined, documentId: number): Promise<PatientSession> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const response = await fetch(`/api/patient/documents/${documentId}`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(credentialsPayload(patientId, password)),
      credentials: 'same-origin',
      signal: controller.signal,
    })
    const body: unknown = await response.json().catch(() => null)
    if (!response.ok) throw new ApiError(response.status, detailFrom(body, 'Unable to delete this report.'))
    return assertWorkspace(body)
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') throw new ApiError(408, 'The request took too long. Try again.')
    throw new ApiError(0, 'Unable to reach ClinicForce. Check your connection and try again.')
  } finally {
    window.clearTimeout(timeout)
  }
}

export async function deletePatientDocuments(
  patientId: string | undefined,
  password: string | undefined,
  documentIds: number[],
): Promise<PatientSession> {
  return assertWorkspace(await request(
    '/api/patient/documents/bulk-delete',
    json({ ...credentialsPayload(patientId, password), document_ids: documentIds }),
  ))
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

export async function decryptAndSummarize(manifestId: string, key: string, passcode: string): Promise<DoctorSummary> {
  return request<DoctorSummary>('/api/doctor/decrypt-and-summarize', json({ manifest_id: manifestId, key, passcode }))
}
