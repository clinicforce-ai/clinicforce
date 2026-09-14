import type { PatientSession } from './patient-models'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export const REQUEST_TIMEOUT_MS = 45_000

let staffIntakeId: string | null = null
export function setStaffIntakeContext(id: string | null) { staffIntakeId = id }


export function detailFrom(body: unknown, fallback: string): string {
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

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const headers = new Headers(init.headers)
    if (staffIntakeId && path.startsWith('/api/doctor/insurance')) headers.set('X-ClinicForce-Intake', staffIntakeId)
    const response = await fetch(path, { ...init, headers, credentials: 'same-origin', signal: controller.signal })
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

export function json(body: object): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export function assertWorkspace(value: unknown): PatientSession {
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
