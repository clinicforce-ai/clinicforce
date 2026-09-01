import type { ManualRecordInput, PatientProfile, PatientProfileInput, PatientSession, RecordCategory } from "../../services/api"
export interface PatientPortalProps {
  onBack: () => void
}

export type AuthMode = 'login' | 'register'
export type WorkspaceTab = 'overview' | 'records' | 'connections'
export type FormState = Record<Exclude<keyof ManualRecordInput, 'category'>, string>
export type ProfileFormState = PatientProfileInput

export const emptyForm: FormState = {
  hospital: '', name: '', value: '', unit: '', date: '', details: '', dosage: '',
}

export const emptyProfileForm: ProfileFormState = {
  given_name: '', family_name: '', birth_date: '', administrative_gender: 'unknown',
}

export function profileFormFrom(profile: PatientProfile): ProfileFormState {
  return {
    given_name: profile.given_name ?? profile.suggested_given_name ?? '',
    family_name: profile.family_name ?? profile.suggested_family_name ?? '',
    birth_date: profile.birth_date ?? '',
    administrative_gender: profile.administrative_gender ?? 'unknown',
  }
}

export function profileIsComplete(profile: PatientProfile | null): boolean {
  return Boolean(profile?.given_name && profile.family_name && profile.birth_date && profile.administrative_gender)
}

export const categoryMeta: Record<RecordCategory, { label: string; help: string; icon: string }> = {
  hospital: { label: 'Care visit', help: 'A hospital, clinic, or care-team visit', icon: '01' },
  lab: { label: 'Lab result', help: 'A test name and result', icon: '02' },
  vital: { label: 'Vital sign', help: 'A measurement such as blood pressure', icon: '03' },
  condition: { label: 'Condition', help: 'A diagnosis or ongoing condition', icon: '04' },
  allergy: { label: 'Allergy', help: 'An allergy or intolerance', icon: '05' },
  medication: { label: 'Medication', help: 'A medication you currently take', icon: '06' },
}

export const externalImportMeta = { label: 'External SHL import', icon: 'SHL' }
export const ecwImportMeta = { label: 'eClinicalWorks / healow import', icon: 'eCW' }
export const ecwImportableResourceTypes = new Set([
  'AllergyIntolerance', 'CarePlan', 'CareTeam', 'Condition', 'Coverage',
  'DiagnosticReport', 'Device', 'DocumentReference', 'Encounter', 'Goal',
  'Immunization', 'MedicationDispense', 'MedicationRequest', 'Observation',
  'Procedure', 'ServiceRequest',
])

export function recordMeta(category: PatientSession['records'][number]['category']): { label: string; icon: string } {
  if (category === 'external-shl') return externalImportMeta
  if (category === 'ecw') return ecwImportMeta
  return categoryMeta[category]
}

export function dateLabel(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export function patientInitials(patientId: string): string {
  return patientId.slice(0, 2).toUpperCase() || 'PT'
}

export function messageFrom(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

export function codedText(value: unknown): string | null {
  if (!value || typeof value !== 'object') return null
  const candidate = value as { text?: unknown; coding?: unknown }
  if (typeof candidate.text === 'string' && candidate.text.trim()) return candidate.text.trim()
  if (Array.isArray(candidate.coding)) {
    for (const coding of candidate.coding) {
      if (!coding || typeof coding !== 'object') continue
      const display = (coding as { display?: unknown; code?: unknown }).display ?? (coding as { code?: unknown }).code
      if (typeof display === 'string' && display.trim()) return display.trim()
    }
  }
  return null
}
