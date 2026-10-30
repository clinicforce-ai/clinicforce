import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { patientBenefitsBound } from './identityBinding'

export function shouldShowSelectedRequirements(
  result: PrevisitEstimate,
  plannedCodes: string[],
  cash: boolean,
  previousEstimate: boolean,
): boolean {
  return (
    !cash &&
    !previousEstimate &&
    plannedCodes.length > 0 &&
    result.benefits?.active === true &&
    result.benefits.service_date_matches !== false &&
    !result.benefits.coverage_status_conflict &&
    !result.benefits.coverage_dates_invalid &&
    !result.benefits.network_conflict &&
    !result.benefits.historical_only &&
    !result.benefits.source_errors?.length &&
    !result.benefits.has_secondary &&
    result.recovery?.reason_code !== 'multiple_active_policies' &&
    patientBenefitsBound(result.identity)
  )
}
