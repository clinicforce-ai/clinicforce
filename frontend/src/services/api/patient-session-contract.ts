import type { PatientSession } from './patient-session-types'
import { ApiError } from './client'

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
