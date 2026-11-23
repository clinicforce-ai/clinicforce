import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { bluecardOfficeDeductibleScenario } from './bluecardOfficeDeductibleScenario'
import { money } from './format'

export function leadingPatientShare(
  result: PrevisitEstimate,
  values: {
    medicarePrimaryShare: number | null | undefined
    automaticCashFallback: boolean
    officeReferenceCopay: number | null
    humanaReferenceCopay: number | null
    cardPlanningCopay: number | null
    dsnpOffice: boolean
    feeProxy: PrevisitEstimate['fee_proxy_estimate']
    bluecardDeductible: ReturnType<typeof bluecardOfficeDeductibleScenario>
    combinedPlan: PrevisitEstimate['combined_emg_plan']
    biopsyTarget: PrevisitEstimate['provisional_collection_target'] | null
    cignaTelevisit: { share: number } | null
  },
): string | null {
  const {
    medicarePrimaryShare,
    automaticCashFallback,
    officeReferenceCopay,
    humanaReferenceCopay,
    cardPlanningCopay,
    dsnpOffice,
    feeProxy,
    bluecardDeductible,
    combinedPlan,
    biopsyTarget,
    cignaTelevisit,
  } = values
  if (medicarePrimaryShare != null)
    return `${money(medicarePrimaryShare)} provisional Medicare share before secondary payment`
  if (automaticCashFallback)
    return officeReferenceCopay != null || humanaReferenceCopay != null
      ? `${money(officeReferenceCopay ?? humanaReferenceCopay!)} office-only reference; collection pending`
      : cardPlanningCopay != null
        ? `${money(cardPlanningCopay)} office-only card suggestion`
        : 'Not fully calculated'
  if (dsnpOffice && feeProxy)
    return `${money(feeProxy.patient_share_cents)} provisional · before Medicaid`
  if (bluecardDeductible) return `${money(bluecardDeductible.patientShare)} estimated`
  if (combinedPlan) return `${money(combinedPlan.patient_share_cents)} planning estimate`
  if (
    [
      'qmb_protection',
      'out_of_pocket_limit',
      'published_geha_pshb_zero_office',
      'healthselect_ma_zero_televisit',
      'healthselect_ma_zero_office_biopsy',
    ].includes(result.collection_guidance?.basis || '') &&
    result.collection_guidance?.amount_cents === 0
  )
    return money(0)
  if (biopsyTarget)
    return `${money(biopsyTarget.amount_cents)} provisional · full deductible assumed${biopsyTarget.pending_codes.length ? ' · partial visit' : ''}`
  if (cignaTelevisit) return `${money(cignaTelevisit.share)} office copay estimate`
  return null
}
