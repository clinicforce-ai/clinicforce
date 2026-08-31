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
