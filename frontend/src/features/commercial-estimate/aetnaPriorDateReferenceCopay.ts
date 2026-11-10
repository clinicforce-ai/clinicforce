import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { patientBenefitsBound } from './identityBinding'
import { singleOfficeCopayLine, specialistOfficeCopay } from './payerReportedPartialCopay'

export function aetnaPriorDateReferenceCopay(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
): number | null {
  const benefits = result.reference_benefits ?? result.benefits
  const line = singleOfficeCopayLine(
    result.price_range?.mode === 'services' ? result.price_range.lines || [] : [],
  )
  const otherCoverage =
    benefits?.has_secondary || result.recovery?.reason_code === 'multiple_active_policies'
  if (
    model.cash ||
    model.previousEstimate ||
    model.calculating ||
    model.amount ||
    result.collection_guidance?.amount_cents != null ||
    !patientBenefitsBound(result.identity) ||
    !/^aetna$/i.test(result.payer?.trim() || '') ||
    benefits?.active !== true ||
    benefits.service_date_matches !== false ||
    benefits.coverage_status_conflict ||
    benefits.coverage_dates_invalid ||
    benefits.network_conflict ||
    benefits.historical_only ||
    benefits.source_errors?.length ||
    (otherCoverage &&
      (benefits.network_status !== 'in_network' ||
        benefits.network_source !== 'payer_participation_message')) ||
    benefits.qmb_status === 'protected' ||
    result.benefits?.qmb_status === 'protected' ||
    result.billing_protection?.status === 'qmb' ||
    result.recovery?.reason_code === 'same_policy_evidence_conflict' ||
    result.price_range?.service_issues?.length ||
    line?.benefits.active !== true ||
    line?.benefits.source_errors?.length ||
    !line
  )
    return null
  const copay = specialistOfficeCopay({ ...result, benefits })
  if (copay != null) return copay === line.benefits.copay_cents ? copay : null
  const selectedId = line.benefits.copay_id
  const individualOfficeRowPresent = benefits.reported_options?.some(
    (row) =>
      row.kind === 'copay' &&
      row.coverage_level === 'INDIVIDUAL' &&
      row.service_type_code
        .split(',')
        .map((code) => code.trim())
        .includes('98') &&
      ['IN_NETWORK', 'IN_AND_OUT_OF_NETWORK'].includes(row.network),
  )
  return result.benefit_source?.startsWith('Stedi') &&
    !individualOfficeRowPresent &&
    selectedId &&
    line.benefits.selected_benefit_ids?.includes(selectedId) &&
    Number.isInteger(line.benefits.copay_cents) &&
    line.benefits.copay_cents! >= 0
    ? line.benefits.copay_cents!
    : null
}
