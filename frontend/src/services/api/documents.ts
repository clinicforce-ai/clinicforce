import { assertWorkspace } from './patient-session-contract'
import { type PatientSession } from './patient-session-types'
import { ApiError, detailFrom, json, request, REQUEST_TIMEOUT_MS } from './client'
import { credentialsPayload } from './patient-credentials'

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
      throw new ApiError(
        response.status,
        detailFrom(body, 'Unable to download the selected reports.'),
      )
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

export async function importGoogleDriveChanges(
  patientId: string | undefined,
  password: string | undefined,
): Promise<PatientSession> {
  return assertWorkspace(
    await request(
      '/api/patient/google-drive/import',
      json(credentialsPayload(patientId, password)),
    ),
  )
}

export async function deletePatientDocument(
  patientId: string | undefined,
  password: string | undefined,
  documentId: number,
): Promise<PatientSession> {
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
    if (!response.ok)
      throw new ApiError(response.status, detailFrom(body, 'Unable to delete this report.'))
    return assertWorkspace(body)
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError')
      throw new ApiError(408, 'The request took too long. Try again.')
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
  return assertWorkspace(
    await request(
      '/api/patient/documents/bulk-delete',
      json({ ...credentialsPayload(patientId, password), document_ids: documentIds }),
    ),
  )
}
