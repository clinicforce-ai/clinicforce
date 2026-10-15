import type { ManualRecordInput } from '../../services/api/patient-document-types'

export type FormState = Record<Exclude<keyof ManualRecordInput, 'category'>, string>

export const emptyForm: FormState = {
  hospital: '',
  name: '',
  value: '',
  unit: '',
  date: '',
  details: '',
  dosage: '',
}
