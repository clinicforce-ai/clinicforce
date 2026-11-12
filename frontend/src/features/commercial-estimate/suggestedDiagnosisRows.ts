import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'

export function suggestedDiagnosisRows(reason: string | null | undefined): CostBreakdownRow[] {
  if (
    /\b(?:paresthesi(?:a|as)|tingling)\b/i.test(reason || '') &&
    /\b(?:right|r\.?)\s+hand\b/i.test(reason || '')
  )
    return [
      {
        label: 'Suggested diagnosis code',
        value: 'ICD-10-CM R20.2 · Paresthesia of skin',
        detail: 'Confirm the symptom and any more specific diagnosis in the clinical record.',
      },
      {
        label: 'Conditional procedure · NCS',
        value: 'CPT 95907–95913 · nerve conduction studies',
        detail:
          'Only if the clinician orders testing. The exact CPT depends on the documented study count; this is not included in the visit estimate.',
      },
      {
        label: 'Conditional procedure · needle EMG',
        value: 'CPT 95885 or 95886 · needle EMG',
        detail:
          'Only if the clinician plans and performs needle testing with NCS on the same day. The examined muscles determine limited versus complete coding; this is not included in the visit estimate.',
      },
    ]
  if (/^\s*bell[’']?s? palsy\s*$/i.test(reason || ''))
    return [
      {
        label: 'Suggested diagnosis code',
        value: 'ICD-10-CM G51.0 · Bell’s palsy',
        detail:
          'The diagnosis does not create a separate procedure CPT. The clinician selects the office E/M level from documented decision making or time.',
      },
    ]
  return /^\s*dizziness\b/i.test(reason || '')
    ? [
        {
          label: 'Suggested diagnosis code',
          value: 'ICD-10-CM R42 · Dizziness and giddiness',
          detail:
            'The clinician confirms the diagnosis and selects the E/M CPT for services performed.',
        },
      ]
    : []
}
