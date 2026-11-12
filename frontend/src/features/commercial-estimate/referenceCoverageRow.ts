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
  const bluecardHome =
    result.network_evidence?.product_network_source === 'signed_bluecard_agreement'
      ? result.plan_details?.stedi?.verification_payer?.name || result.network_evidence.home_plan
      : null
  const coverageDetail = bluecardHome
    ? `${detail} · ${bluecardHome} is the BlueCard home plan; BCBS Texas is the local route`
    : detail
  return {
    label: 'Coverage',
    value,
    detail:
      order?.status === 'verified'
        ? `${coverageDetail} · ${result.payer || 'Selected plan'} primary · ${order.secondary_payer} secondary`
        : benefits?.medicare_part_b_secondary?.status === 'reported'
          ? `${coverageDetail} · Payer reports Original Medicare Part B as secondary; claim order not independently verified`
          : coverageDetail,
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

export function networkRow(
  model: CommercialEstimateModel,
  dsnpOffice: boolean,
  result: PrevisitEstimate,
): CostBreakdownRow {
  const texasEpo =
    /(?:blue cross.*blue shield.*(?:tx|texas)|bcbs\s*(?:tx|texas))/i.test(result.payer || '') &&
    result.plan_details?.stedi?.names?.some((name) => /\bEPO\b/i.test(name))
  return {
    label: 'Network',
    value:
      dsnpOffice && model.networkLabel === 'Not determined'
        ? 'Office benefit in/out of network · provider participation unknown'
        : model.networkLabel,
    detail:
      texasEpo &&
      result.network_evidence?.product_network_source === 'signed_bluecard_agreement' &&
      model.networkLabel.startsWith('In network')
        ? 'Signed Texas BlueCard agreement and the scoped clinic provider record support this in-network EPO visit.'
        : texasEpo && model.networkLabel === 'Patient plan not matched'
          ? 'BCBSTX EPO uses the Blue Choice PPO network. Reconcile the subscriber identity, then confirm the clinic agreement and this provider’s participation for the member plan and visit.'
          : undefined,
  }
}

export function payerOrderRow(
  result: PrevisitEstimate,
  benefits: PrevisitBenefits,
): CostBreakdownRow | null {
  const verified = benefits?.primary_secondary_order
  if (verified?.status === 'verified')
    return {
      label: 'Payer order',
      value: `${result.payer || 'Selected payer'} primary · ${verified.secondary_payer} secondary`,
      detail:
        'eCW policy sequence and the primary payer response agree; secondary payment remains unknown.',
    }
  const reported = benefits?.reported_secondary_payer
  if (reported?.status === 'reported')
    return {
      label: 'Payer order',
      value: `${result.payer || 'Selected payer'} 271 lists ${reported.payer} as secondary`,
      detail: 'Secondary entitlement and claim order have not been independently verified.',
    }
  return benefits?.has_secondary || result.recovery?.reason_code === 'multiple_active_policies'
    ? { label: 'Payer order', value: 'Multiple active policies · order not verified' }
    : null
}
