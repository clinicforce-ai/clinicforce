import {
  type StaffCardDetails,
  type StaffIntake,
  type StaffPatientFields,
} from '../../services/api/staff-contracts'

export const emptyFields: StaffPatientFields = {
  given_name: '',
  family_name: '',
  birth_date: '',
  member_id: '',
  payer: '',
  group_number: '',
  relationship: 'unknown',
  subscriber_name: '',
  subscriber_birth_date: '',
}
export const emptyCardDetails: StaffCardDetails = { full_name: '', member_id: '', birth_date: '' }
export const patientName = (intake: StaffIntake) =>
  [
    intake.patient?.given_name || intake.fields.given_name,
    intake.patient?.family_name || intake.fields.family_name,
  ]
    .filter(Boolean)
    .join(' ') || 'Patient details needed'
export const statusLabel = (status: string) =>
  (
    ({
      needs_confirmation: 'Confirm patient',
      ready: 'Ready to review',
      in_progress: 'Review in progress',
      reviewed: 'Staff review complete',
    }) as Record<string, string>
  )[status] || status.replaceAll('_', ' ')
export const dateLabel = (value: string) =>
  new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
export const today = () => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
export function patientFieldsFrom(record: StaffIntake): StaffPatientFields {
  const fields = Object.fromEntries(
    Object.entries(emptyFields).map(([key, fallback]) => {
      const value = record.fields[key as keyof StaffPatientFields]
      return [key, typeof value === 'string' ? value : fallback]
    }),
  ) as StaffPatientFields
  fields.subscriber_name ||= record.fields.member_name || ''
  if (!fields.given_name && !fields.family_name && record.fields.member_name) {
    const printed = record.fields.member_name.trim()
    const parts = printed.includes(',')
      ? printed.split(',').reverse().join(' ').trim().split(/\s+/)
      : printed.split(/\s+/)
    if (parts.length > 1) {
      fields.given_name = parts.slice(0, -1).join(' ')
      fields.family_name = parts.at(-1)!
    }
  }
  fields.relationship =
    ({ '18': 'self', '01': 'spouse', '19': 'child', G8: 'other' } as Record<string, string>)[
      fields.relationship
    ] ||
    fields.relationship ||
    'unknown'
  return fields
}
