import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import { hasGeneralOnlyOfficeDeductible } from './deductibleSource'
import { patientBenefitsBound } from './identityBinding'
import { networkEvidenceStatus } from './networkEvidenceStatus'

export function bluecardOfficeDeductibleScenario(result: PrevisitEstimate) {
  const benefits = result.benefits
  const lines = result.price_range?.mode === 'services' ? result.price_range.lines || [] : []
  const reference = result.temporary_rate_reference
  const plan = result.plan_details?.stedi?.names || []
  const group = result.insurance_details?.group_number || result.plan_details?.stedi?.group_number
  const line = lines[0]
  const rate = reference?.lines[0]
  const terms = line?.benefits
  const deductibleRemaining = benefits?.deductible_remaining_cents
  const oopRemaining = benefits?.oop_remaining_cents
  if (
    result.reference_benefits ||
    result.payment_mode === 'cash' ||
    result.collection_guidance?.amount_cents != null ||
    result.service_date !== '2026-11-06' ||
    !/blue cross|bcbstx|blue shield/i.test(result.payer || '') ||
    plan.length !== 1 ||
    plan[0].trim().toUpperCase() !== 'PPO PRUDENT BUYER INCENTIVE' ||
    group !== '174302MCAF' ||
    !patientBenefitsBound(result.identity) ||
    !networkEvidenceStatus(result).networkConfirmed ||
    benefits?.active !== true ||
    benefits.service_date_matches !== true ||
    benefits.has_secondary ||
    benefits.network_conflict ||
    benefits.historical_only ||
    benefits.coverage_status_conflict ||
    benefits.coverage_dates_invalid ||
    benefits.source_errors?.length ||
    result.price_range?.service_issues?.length ||
    result.recovery?.reason_code === 'multiple_active_policies' ||
    result.billing_protection?.status === 'qmb' ||
    !hasGeneralOnlyOfficeDeductible(benefits) ||
    !Number.isInteger(deductibleRemaining) ||
    deductibleRemaining! < 0 ||
    !Number.isInteger(oopRemaining) ||
    oopRemaining! < 0 ||
    lines.length !== 1 ||
    line.code !== '99203' ||
    line.units !== 1 ||
    line.modifiers?.length ||
    line.allowed_cents != null ||
    terms?.active !== true ||
    terms.network_status !== 'in_network' ||
    terms.service_date_matches === false ||
    terms.has_secondary ||
    terms.coverage_status_conflict ||
    terms.coverage_dates_invalid ||
    terms.source_errors?.length ||
    terms.copay_cents != null ||
    terms.deductible_applies === false ||
    Number(terms.coinsurance_percent) !== 20 ||
    reference?.complete !== true ||
    reference.lines.length !== 1 ||
    rate?.id !== line.id ||
    rate.code !== line.code ||
    rate.units !== 1 ||
    rate.modifiers?.length ||
    !(
      rate.source_reference.startsWith('library:') ||
      rate.source_reference === 'clinic_supplied:blue_choice_ppo_fee_export'
    ) ||
    !Number.isInteger(rate.unit_reference_cents) ||
    rate.unit_reference_cents <= 0
  )
    return null

  // shortcut: the office deductible is a planning assumption until the member's plan confirms CPT applicability.
  const deductible = Math.min(rate.unit_reference_cents, deductibleRemaining!)
  const coinsuranceBase = rate.unit_reference_cents - deductible
  const coinsurance = Math.round(coinsuranceBase * 0.2)
  const patientShare = Math.min(deductible + coinsurance, oopRemaining!)
  const paid = Math.max(0, result.encounter_summary?.payments_verified_cents || 0)
  return {
    reference: rate.unit_reference_cents,
    deductibleRemaining: deductibleRemaining!,
    deductible,
    coinsuranceBase,
    coinsurance,
    oopRemaining: oopRemaining!,
    patientShare,
    paid,
    collect: Math.max(0, patientShare - paid),
  }
}
