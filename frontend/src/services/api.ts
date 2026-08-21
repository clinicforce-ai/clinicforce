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
  category: RecordCategory | 'external-shl'
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

export interface PatientProfile {
  given_name: string | null
  family_name: string | null
  birth_date: string | null
  administrative_gender: 'male' | 'female' | 'other' | 'unknown' | null
  suggested_given_name: string | null
  suggested_family_name: string | null
}

export interface PatientProfileInput {
  given_name: string
  family_name: string
  birth_date: string
  administrative_gender: 'male' | 'female' | 'other' | 'unknown'
}

export interface LinkResponse {
  shl_uri: string
  qr_code_base64: string
  epic_included?: boolean
  share_mode?: 'standard' | 'ecw_compat'
}

export interface EpicEndpoint {
  id: string
  name: string
  aliases: string[]
  addresses: Array<Record<string, unknown>>
}

export interface FhirResource {
  resourceType?: string
  id?: string
  [key: string]: unknown
}

export interface EpicRecord {
  bundle: { resourceType: string; entry: Array<{ resource: FhirResource }> }
  availability: Record<string, string>
  source: { organization_name: string; retrieved_at: string }
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
  source_filename?: string
  page_number?: number
  document_id?: number
  download_url?: string
  source_type?: 'epic'
  organization_name?: string
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
  passcode: string | undefined,
  shareMode: 'standard' | 'ecw_compat' = 'standard',
): Promise<LinkResponse> {
  const requestBody = {
    ...credentialsPayload(patientId, password),
    share_mode: shareMode,
    ...(shareMode === 'standard' ? { passcode } : {}),
  }
  const value = await request<unknown>(
    '/api/patient/generate-link',
    json(requestBody),
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

export interface ShlManifestFile {
  contentType: 'application/fhir+json' | 'application/smart-health-card' | 'application/smart-api-access'
  embedded?: string
  location?: string
}

const EXTERNAL_SHL_MAX_BYTES = 5 * 1024 * 1024

async function readExternalText(response: Response): Promise<string> {
  const declaredLength = Number(response.headers.get('content-length') ?? '0')
  if (Number.isFinite(declaredLength) && declaredLength > EXTERNAL_SHL_MAX_BYTES) {
    throw new ApiError(413, 'The external Smart Health Link response exceeds the 5 MB safety limit.')
  }
  if (!response.body) return response.text()
  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let length = 0
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    length += value.byteLength
    if (length > EXTERNAL_SHL_MAX_BYTES) {
      await reader.cancel()
      throw new ApiError(413, 'The external Smart Health Link response exceeds the 5 MB safety limit.')
    }
    chunks.push(value)
  }
  const content = new Uint8Array(length)
  let offset = 0
  for (const chunk of chunks) { content.set(chunk, offset); offset += chunk.byteLength }
  return new TextDecoder().decode(content)
}

async function externalFetch(url: string, init: RequestInit): Promise<Response> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 20_000)
  try {
    return await fetch(url, {
      ...init,
      credentials: 'omit',
      redirect: 'error',
      referrerPolicy: 'no-referrer',
      signal: controller.signal,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw new ApiError(408, 'The external issuer did not respond within 20 seconds.')
    throw new ApiError(0, 'The external issuer could not be reached from this browser. It may not allow cross-origin access.')
  } finally {
    window.clearTimeout(timeout)
  }
}

function externalHttpsUrl(value: string, label: string): URL {
  let url: URL
  try { url = new URL(value) } catch { throw new ApiError(502, `The issuer supplied an invalid ${label} URL.`) }
  if (url.protocol !== 'https:' || url.username || url.password) throw new ApiError(502, `The issuer supplied a non-HTTPS ${label} URL.`)
  return url
}

export async function fetchManifest(
  manifestUrl: string,
  recipient: string,
  passcode: string | undefined,
  embeddedLengthMax = 1_000_000,
): Promise<ShlManifestFile[]> {
  const url = externalHttpsUrl(manifestUrl, 'manifest')
  const response = await externalFetch(url.toString(), {
    method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ recipient, ...(passcode ? { passcode: passcode.trim() } : {}), embeddedLengthMax }),
  })
  const text = await readExternalText(response)
  let payload: unknown
  try { payload = JSON.parse(text) } catch { throw new ApiError(response.status || 502, 'The external issuer returned an invalid SHL manifest.') }
  if (!response.ok) {
    if (response.status === 401 && payload && typeof payload === 'object' && 'remainingAttempts' in payload) {
      const remaining = (payload as { remainingAttempts?: unknown }).remainingAttempts
      throw new ApiError(401, `The passcode was rejected.${typeof remaining === 'number' ? ` ${remaining} attempt${remaining === 1 ? '' : 's'} remain.` : ''}`)
    }
    if (response.status === 404) throw new ApiError(404, 'This Smart Health Link is no longer active. Ask the issuer for a new code.')
    throw new ApiError(response.status || 502, 'The external issuer rejected the Smart Health Link request.')
  }
  const files = payload && typeof payload === 'object' && 'files' in payload ? (payload as { files?: unknown }).files : undefined
  if (!Array.isArray(files) || !files.length) throw new ApiError(502, 'The external issuer returned no SHL files.')
  return files.map((file) => {
    if (!file || typeof file !== 'object') throw new ApiError(502, 'The external issuer returned an invalid SHL file.')
    const item = file as Record<string, unknown>
    if (item.contentType !== 'application/fhir+json' && item.contentType !== 'application/smart-health-card' && item.contentType !== 'application/smart-api-access') {
      throw new ApiError(415, 'This Smart Health Link contains an unsupported file type.')
    }
    if (typeof item.embedded !== 'string' && typeof item.location !== 'string') throw new ApiError(502, 'The external issuer returned a file without encrypted content.')
    if (typeof item.location === 'string') externalHttpsUrl(item.location, 'file location')
    return { contentType: item.contentType, ...(typeof item.embedded === 'string' ? { embedded: item.embedded } : {}), ...(typeof item.location === 'string' ? { location: item.location } : {}) }
  })
}

export async function fetchExternalShlJwe(location: string): Promise<string> {
  const response = await externalFetch(externalHttpsUrl(location, 'file location').toString(), { method: 'GET', headers: { Accept: 'application/jose' } })
  const jwe = await readExternalText(response)
  if (!response.ok) throw new ApiError(response.status || 502, 'The external encrypted file could not be retrieved.')
  return jwe
}

export async function importExternalShl(bundle: Record<string, unknown>, sourceHost: string): Promise<PatientSession> {
  return assertWorkspace(await request('/api/patient/external-shl-import', json({ source_host: sourceHost, bundle })))
}

export async function decryptAndSummarize(manifestId: string, key: string, passcode: string): Promise<DoctorSummary> {
  return request<DoctorSummary>('/api/doctor/decrypt-and-summarize', json({ manifest_id: manifestId, key, passcode }))
}
