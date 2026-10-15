import { type ClinicalPatient } from '../../services/api/staff-patients'

export const name = (patient: ClinicalPatient) =>
  `${patient.given_name} ${patient.family_name}`.trim() || 'Unconfirmed card upload'

export const accountLabel = (value: ClinicalPatient['account_status']) =>
  value === 'portal'
    ? 'Shared patient account'
    : value === 'automatic'
      ? 'Insurance intake record'
      : 'Clinic patient record'

export const stateLabel = (value: string) =>
  (
    ({
      needs_confirmation: 'Confirm patient',
      ready: 'Ready for review',
      in_progress: 'Review in progress',
      reviewed: 'Staff review complete',
    }) as Record<string, string>
  )[value] || value.replaceAll('_', ' ')
