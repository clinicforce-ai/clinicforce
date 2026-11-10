import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'

export function suggestedDiagnosisRows(reason: string | null | undefined): CostBreakdownRow[] {
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
