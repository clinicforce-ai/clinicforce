import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { PrevisitBenefits } from '../../services/api/commercial-benefits'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { visitDate } from './format'

export function referenceCoverageRow(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
  benefits: PrevisitBenefits,
): CostBreakdownRow {
  const subscriber = result.reference_subscriber
  const requestedDate = result.service_date ? visitDate(result.service_date) : 'date unavailable'
  const value = subscriber?.first_name
    ? [
        result.payer || 'Payer not recorded',
        `Stedi subscriber ${subscriber.first_name} ${subscriber.last_name || ''}`.trim(),
        subscriber.member_id ? `member ID ending ${subscriber.member_id.slice(-4)}` : '',
      ]
        .filter(Boolean)
        .join(' · ')
    : [result.payer || 'Payer not recorded', model.planName].filter(Boolean).join(' · ')
  const detail = result.reference_benefits
    ? `Stedi subscriber and benefit values used for this reference; eCW identity differs · Stedi requested ${requestedDate}; payer returned service date ${benefits?.returned_service_dates?.start ? visitDate(benefits.returned_service_dates.start) : 'unavailable'}`
    : benefits?.service_date_matches === false
      ? `${model.policyStatus} · Visit ${requestedDate}; payer response ${benefits.returned_service_dates?.start ? visitDate(benefits.returned_service_dates.start) : 'date differs'} · reference estimate only`
      : result.service_date
        ? `${model.policyStatus} · Visit ${requestedDate}`
        : model.policyStatus
  return { label: 'Coverage', value, detail }
}
