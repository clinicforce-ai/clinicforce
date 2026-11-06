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
  const order = benefits?.primary_secondary_order
  return {
    label: 'Coverage',
    value,
    detail:
      order?.status === 'verified'
        ? `${detail} · ${result.payer || 'Selected plan'} primary · ${order.secondary_payer} secondary`
        : detail,
  }
}

export function memberGroupRow(result: PrevisitEstimate): CostBreakdownRow | null {
  if (result.reference_benefits) return null
  const member = result.insurance_details?.member_id
  const group = result.insurance_details?.group_number || result.plan_details?.stedi?.group_number
  if (!member && !group) return null
  return {
    label: 'Member and group',
    value: [member && `Member ${member}`, group && `Group ${group}`].filter(Boolean).join(' · '),
  }
}
