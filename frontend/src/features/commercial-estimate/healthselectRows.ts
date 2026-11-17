import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import { commercialRequirementRows } from './commercialRequirementStatus'
import { money } from './format'

export function healthselectOfficeBiopsy(result: PrevisitEstimate): boolean {
  return (
    result.collection_guidance?.basis === 'healthselect_ma_zero_office_biopsy' &&
    result.collection_guidance.amount_cents === 0
  )
}

export function healthselectZero(result: PrevisitEstimate): boolean {
  return (
    (result.collection_guidance?.basis === 'healthselect_ma_zero_televisit' ||
      healthselectOfficeBiopsy(result)) &&
    result.collection_guidance?.amount_cents === 0
  )
}

export function healthselectCopayDetail(result: PrevisitEstimate): string {
  if (healthselectOfficeBiopsy(result))
    return result.card_check?.independent_ecw_check
      ? 'UHC reports $0 office and surgical copays. The saved card differs from the selected eCW policy and was not used for this payer check.'
      : 'UHC reports $0 office and surgical copays for the proposed covered visit.'
  return result.card_check?.independent_ecw_check
    ? 'Fresh UHC virtual and specialist benefit: $0 copay. The saved card differs from the selected eCW policy and was not used for this payer check.'
    : 'Fresh UHC virtual and specialist benefit: $0 copay. The card also prints $0 for a specialist.'
}

export function healthselectShareDetail(
  result: PrevisitEstimate,
  clinicReference: number | null,
): string {
  return healthselectOfficeBiopsy(result)
    ? `$0.00 office copay + $0.00 surgical copay + $0.00 published deductible + 0% × ${clinicReference == null ? 'UHC allowance' : money(clinicReference) + ' clinic Medicare fee reference'} = $0.00 for the proposed covered office visit and biopsy. Biopsy authorization, payer order and final claim remain open.`
    : '$0.00 copay + $0.00 deductible + 0% × UHC allowed amount = $0.00 for one covered virtual E/M visit. Final code, payer order and claim are unresolved.'
}

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
