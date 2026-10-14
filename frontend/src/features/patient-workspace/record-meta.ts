import type { PatientSession } from '../../services/api/patient-session-types'
import type { RecordCategory } from '../../services/api/patient-document-types'

export const categoryMeta: Record<RecordCategory, { label: string; help: string; icon: string }> = {
  hospital: { label: 'Care visit', help: 'A hospital, clinic, or care-team visit', icon: '01' },
  lab: { label: 'Lab result', help: 'A test name and result', icon: '02' },
  vital: { label: 'Vital sign', help: 'A measurement such as blood pressure', icon: '03' },
  condition: { label: 'Condition', help: 'A diagnosis or ongoing condition', icon: '04' },
  allergy: { label: 'Allergy', help: 'An allergy or intolerance', icon: '05' },
  medication: { label: 'Medication', help: 'A medication you currently take', icon: '06' },
}

const externalImportMeta = { label: 'External SHL import', icon: 'SHL' }

const ecwImportMeta = { label: 'eClinicalWorks / healow import', icon: 'eCW' }

export const ecwImportableResourceTypes = new Set([
  'AllergyIntolerance',
  'CarePlan',
  'CareTeam',
  'Condition',
  'Coverage',
  'DiagnosticReport',
  'Device',
  'DocumentReference',
  'Encounter',
  'Goal',
  'Immunization',
  'MedicationDispense',
  'MedicationRequest',
  'Observation',
  'Procedure',
  'ServiceRequest',
])

export function recordMeta(category: PatientSession['records'][number]['category']): {
  label: string
  icon: string
} {
  if (category === 'external-shl') return externalImportMeta
  if (category === 'ecw') return ecwImportMeta
  return categoryMeta[category]
}
