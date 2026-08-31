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
  category: RecordCategory | 'external-shl' | 'ecw'
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

export interface EcwRecord {
  bundle: { resourceType: string; entry: Array<{ resource: FhirResource }> }
  availability: Record<string, string>
  source: { organization_name: string; practice_code: string; retrieved_at: string }
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
