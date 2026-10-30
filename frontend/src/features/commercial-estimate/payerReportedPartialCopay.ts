import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { VisitPricedLine } from '../../services/api/commercial-visit-types'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { patientBenefitsBound } from './identityBinding'
import { firstSelectedCptIssue } from './selectedCptIssue'
import { networkEvidenceStatus } from './networkEvidenceStatus'

export function cardOfficePlanningCopay(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
): number | null {
  const copay = result.card_check?.office_copay_cents
  const officeLines =
    result.price_range?.mode === 'services'
      ? result.price_range.lines?.filter(
          (line) => /^9921[2-5]$/.test(line.code) && line.units === 1,
        ) || []
      : []
  return (!model.cash || !!result.cash_fallback) &&
    !model.calculating &&
    !model.previousEstimate &&
    !model.supportedAmount &&
    result.card_check?.status !== 'conflict' &&
    result.collection_guidance?.amount_cents == null &&
    result.collection_guidance?.basis !== 'qmb_protection' &&
    result.billing_protection?.status !== 'qmb' &&
    !result.benefits?.has_secondary &&
    !result.encounter_summary?.payments_verified_cents &&
    officeLines.length === 1 &&
    Number.isInteger(copay) &&
    copay! >= 0
    ? copay!
    : null
}

export function specialistOfficeCopay(
  result: PrevisitEstimate,
  networks = ['IN_NETWORK', 'IN_AND_OUT_OF_NETWORK'],
): number | null {
  const amounts = new Set(
    (result.benefits?.active === true ? result.benefits.reported_options || [] : [])
      .filter(
        (row) =>
          row.kind === 'copay' &&
          row.service_type_code
            .split(',')
            .map((code) => code.trim())
            .includes('98') &&
          row.coverage_level === 'INDIVIDUAL' &&
          networks.includes(row.network) &&
          row.places_of_service?.includes('11') &&
          /\bSpecialist Visit or Evaluation\b/i.test(row.payer_note || '') &&
          !/telemedicine|teladoc|virtual/i.test(row.payer_note || '') &&
          row.plan_association !== 'supplementary' &&
          Number.isInteger(row.amount_cents) &&
          row.amount_cents! >= 0,
      )
      .map((row) => row.amount_cents!),
  )
  return amounts.size === 1 ? [...amounts][0] : null
}

export function specialistOfficeCoinsurance(
  result: PrevisitEstimate,
  networks = ['IN_NETWORK', 'IN_AND_OUT_OF_NETWORK'],
): string | null {
  const percents = new Set(
    (result.benefits?.active === true ? result.benefits.reported_options || [] : [])
      .filter(
        (row) =>
          row.kind === 'coinsurance' &&
          row.service_type_code
            .split(',')
            .map((code) => code.trim())
            .includes('98') &&
          row.coverage_level === 'INDIVIDUAL' &&
          networks.includes(row.network) &&
          row.places_of_service?.includes('11') &&
          row.plan_association !== 'supplementary' &&
          row.percent != null,
      )
      .map((row) => row.percent!),
  )
  return percents.size === 1 ? [...percents][0] : null
}

export function aetnaOfficeReferenceCopay(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
): number | null {
  const benefits = result.reference_benefits ?? result.benefits
  const copay = specialistOfficeCopay(
    { ...result, benefits },
    benefits?.network_status === 'in_network' ? undefined : ['IN_AND_OUT_OF_NETWORK'],
  )
  const coinsurance = specialistOfficeCoinsurance(
    { ...result, benefits },
    benefits?.network_status === 'in_network' ? undefined : ['IN_AND_OUT_OF_NETWORK'],
  )
  if (
    !model.cash ||
    model.previousEstimate ||
    !result.cash_fallback ||
    !/\baetna\b/i.test([result.stedi_payer?.name, result.payer].filter(Boolean).join(' ')) ||
    !result.service_date ||
    benefits?.active !== true ||
    benefits.network_status === 'out_of_network' ||
    benefits.network_conflict ||
    benefits.has_secondary ||
    benefits.source_errors?.length ||
    benefits.qmb_status === 'protected' ||
    result.billing_protection?.status === 'qmb' ||
    benefits.deductible_remaining_cents !== 0 ||
    !/^0(?:\.0+)?$/.test(coinsurance || '') ||
    !Number.isInteger(benefits.oop_remaining_cents) ||
    benefits.oop_remaining_cents! < 0 ||
    copay == null
  )
    return null
  return benefits.network_status === 'in_network'
    ? Math.min(copay, benefits.oop_remaining_cents!)
    : copay
}

export function aetnaOfficePlanningCopay(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
  calculated: boolean,
  lines: VisitPricedLine[],
): number | null {
  const benefits = result.reference_benefits ?? result.benefits
  const officeLine = lines.length === 1 ? lines[0] : null
  const terms = officeLine?.benefits
  return !model.cash &&
    !calculated &&
    /^aetna$/i.test(result.payer || '') &&
    officeLine?.units === 1 &&
    /^(9920[2-5]|9921[2-5])$/.test(officeLine.code) &&
    benefits?.active === true &&
    benefits.network_status === 'in_network' &&
    benefits.network_source === 'payer_participation_message' &&
    benefits.service_date_matches !== false &&
    !benefits.has_secondary &&
    !benefits.coverage_status_conflict &&
    !benefits.coverage_dates_invalid &&
    !benefits.source_errors?.length &&
    terms?.active === true &&
    terms.network_status === 'in_network' &&
    terms.service_date_matches !== false &&
    !terms.coverage_status_conflict &&
    !terms.coverage_dates_invalid &&
    !terms.source_errors?.length &&
    !!terms.selected_benefit_ids?.length &&
    terms.copay_cents != null &&
    terms.coinsurance_percent != null &&
    Number(terms.coinsurance_percent) === 0 &&
    (terms.deductible_applies === false || terms.deductible_remaining_cents === 0) &&
    terms.oop_remaining_cents != null &&
    terms.oop_remaining_cents >= terms.copay_cents
    ? terms.copay_cents
    : null
}

export function payerReportedPartialCopay(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
) {
  const benefits = result.benefits
  const line = model.officeCopayLine
  const payer = [result.stedi_payer?.name, result.payer].filter(Boolean).join(' ')
  const plans = result.plan_details?.stedi.names || []
  const officeLines = result.price_range?.lines?.filter((item) =>
    /^992(?:0[2-5]|1[2-5])$/.test(item.code),
  )
  if (
    model.cash ||
    model.calculating ||
    model.previousEstimate ||
    model.amount ||
    model.estimatedShare ||
    model.provisionalAmount != null ||
    !/\b(?:uhc|united\s*health(?:care)?)\b/i.test(payer) ||
    plans.length !== 1 ||
    !/(?:\bmedicare(?:\s+advantage)?\s+ppo\b|\b(?:rppo|lppo)\b)/i.test(plans[0]) ||
    result.price_range?.mode !== 'services' ||
    result.price_range.service_issues?.length ||
    officeLines?.length !== 1 ||
    benefits?.active !== true ||
    benefits.service_date_matches === false ||
    benefits.coverage_status_conflict ||
    benefits.coverage_dates_invalid ||
    benefits.network_conflict ||
    benefits.historical_only ||
    benefits.source_errors?.length ||
    benefits.has_secondary ||
    benefits.qmb_status === 'protected' ||
    result.billing_protection?.status === 'qmb' ||
    result.recovery?.reason_code === 'multiple_active_policies' ||
    !patientBenefitsBound(result.identity) ||
    firstSelectedCptIssue(result) ||
    line?.benefits.active !== true ||
    Number(line.units) !== 1 ||
    line.benefits.source_errors?.length ||
    !['', 'VISIT', '27', 'DAY', '7', 'SERVICE', 'PROCEDURE'].includes(
      line.benefits.copay_period || '',
    ) ||
    !(
      line.benefits.network_independent ||
      (networkEvidenceStatus(result).networkConfirmed &&
        line.benefits.network_status === 'in_network')
    )
  )
    return null
  const copay = line.benefits.copay_cents
  return typeof copay === 'number' && Number.isInteger(copay) && copay > 0
    ? Math.max(0, copay - (result.encounter_summary?.payments_verified_cents || 0))
    : null
}
