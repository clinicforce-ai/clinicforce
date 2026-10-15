import type { PatientDocument, PatientRecord } from './patient-document-types'

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
