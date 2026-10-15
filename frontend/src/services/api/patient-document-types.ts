export type RecordCategory = 'hospital' | 'lab' | 'vital' | 'condition' | 'allergy' | 'medication'

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
