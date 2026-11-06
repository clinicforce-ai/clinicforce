import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'

export function suggestedDiagnosisRows(reason: string | null | undefined): CostBreakdownRow[] {
  return /^\s*dizziness\s*$/i.test(reason || '')
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
