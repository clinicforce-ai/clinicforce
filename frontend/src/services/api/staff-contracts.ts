export interface StaffSession {
  id: string
  username: string
  display_name: string
  practice_id: string
  practice_name: string
  role: 'staff' | 'admin'
}

export interface StaffPatient {
  id: string
  ecw_patient_id?: string | null
  ecw_account_number?: string | null
  given_name: string
  family_name: string
  birth_date: string
}

export interface StaffPatientFields {
  given_name: string
  family_name: string
  birth_date: string
  member_id: string
  payer: string
  group_number: string
  relationship: string
  subscriber_name: string
  subscriber_birth_date: string
}

export interface StaffCardDetails {
  full_name: string
  member_id: string
  birth_date: string
}

export interface StaffIntake {
  id: string
  status: string
  source?: 'insurance_card' | 'manual_entry' | 'ecw_lookup' | 'clinicforce_qr' | 'patient_upload'
  auto_patient_id?: string | null
  auto_patient?: (StaffPatient & { identity_status?: 'unverified' | 'confirmed' }) | null
  expected_patient_id?: string | null
  expected_patient?: StaffPatient | null
  created_at: string
  updated_at: string
  patient?: StaffPatient | null
  fields: Partial<StaffPatientFields> & {
    member_name?: string
    middle_initial?: string
    ecw_patient_id?: string
  }
  documents: Array<{
    id: string
    filename: string
    content_type: string
    size_bytes: number
    created_at: string
  }>
  workflow_documents?: StaffWorkflowDocument[]
  workflows?: { v1?: string; v2?: string }
  events?: Array<{ id: string; action: string; created_at: string; actor?: string }>
  warning?: string | null
  field_sources?: Record<string, string>
}

export interface StaffWorkflowDocument {
  id: string
  version: 'v1' | 'v2'
  kind: string
  filename: string
  content_type: string
  observed_at: string | null
  download_url: string
}
