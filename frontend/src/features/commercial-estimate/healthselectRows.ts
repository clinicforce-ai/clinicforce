import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { commercialRequirementRows } from './commercialRequirementStatus'

export function healthselectEvidenceRows(
  result: PrevisitEstimate,
  healthselectZero: boolean,
): { card: CostBreakdownRow[]; coding: CostBreakdownRow[] } {
  return {
    card:
      healthselectZero && result.card_check?.independent_ecw_check
        ? [
            {
              label: 'Insurance card',
              value: 'Saved card differs from the selected eCW policy',
              detail:
                'The payer check used the selected first eCW policy and its member ID. Review the card discrepancy; the $0 arrival amount comes from the matching payer response and published plan rule.',
            },
          ]
        : healthselectZero && result.card_check?.status === 'matched'
        ? [
            {
              label: 'Insurance card',
              value: 'HealthSelect MA PPO · H2001-817 · $0 specialist copay',
              detail:
                'Patient-bound card matched the member and group; the payer response supplies visit-date benefits.',
            },
          ]
        : [],
    coding:
      healthselectZero && result.price_range?.lines?.[0]?.code === '98006'
        ? [
            {
              label: 'Medicare billing code',
              value: 'CPT 98006 needs coding review',
              detail:
                'CMS assigns 98006 status I for Medicare payment. The documented service, modality, MDM or time must support the billed E/M code; prior visits do not set today’s level.',
            },
          ]
        : [],
  }
}

export function healthselectRequirementRows(
  result: PrevisitEstimate,
  aetnaRateReference: boolean,
  cignaCardReferral: boolean,
  healthselectZero: boolean,
): CostBreakdownRow[] {
  return commercialRequirementRows(result, aetnaRateReference, cignaCardReferral).map((row) =>
    !healthselectZero
      ? row
      : row.label === 'Referral'
        ? {
            ...row,
            value: 'Not required for this Group Medicare Advantage PPO',
            detail:
              'UnitedHealthcare’s 2026 provider guide says Group Medicare Advantage PPO specialist referrals are not needed.',
          }
        : row.label === 'Prior authorization'
          ? {
              ...row,
              value: 'No routine E/M authorization listed · coding review pending',
              detail:
                'The October 2026 UHC MA prior-authorization list omits 98006 and 99214; the actual billable code still depends on the documented service.',
            }
          : row,
  )
}
