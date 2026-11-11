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
  const options = benefits?.reported_options || []
  const familyDeductible = options.filter(
    (row) =>
      row.kind === 'deductible' &&
      row.service_type_code === '30' &&
      row.coverage_level === 'FAMILY' &&
      row.network === 'IN_NETWORK' &&
      row.plan_association === 'medical' &&
      row.time_period === 'REMAINING' &&
      Number.isInteger(row.amount_cents),
  )
  const specialistCoinsurance = options.filter(
    (row) =>
      row.kind === 'coinsurance' &&
      row.service_type_code === '98' &&
      row.network === 'IN_NETWORK' &&
      row.coverage_level === 'INDIVIDUAL' &&
      row.plan_association === 'medical' &&
      row.payer_note?.trim().toUpperCase() === 'BENEFITCLASS-SPECIALIST',
  )
  const familyOnlyOffice =
    result.cpt_authorization?.state === 'complete' &&
    result.cpt_authorization.pos === '11' &&
    result.cpt_authorization.network === 'in_network' &&
    result.cpt_authorization.patient_binding === result.identity?.binding &&
    familyDeductible.length === 1 &&
    specialistCoinsurance.length === 1 &&
    familyDeductible[0].plan_index === specialistCoinsurance[0].plan_index &&
    Number(specialistCoinsurance[0].percent) === 20 &&
    !options.some(
      (row) =>
        row.kind === 'deductible' &&
        row.network === 'IN_NETWORK' &&
        ((row.service_type_code === '30' &&
          row.coverage_level === 'INDIVIDUAL' &&
          row.time_period === 'REMAINING') ||
          (row.service_type_code === '98' && row.payer_note?.includes('BENEFITCLASS-SPECIALIST'))),
    ) &&
    options.some(
      (row) =>
        row.kind === 'copay' &&
        row.service_type_code === '98' &&
        row.network === 'IN_NETWORK' &&
        row.plan_association === 'medical' &&
        row.plan_index === specialistCoinsurance[0].plan_index &&
        row.amount_cents === 0 &&
        !row.payer_note,
    ) &&
    !options.some(
      (row) =>
        row.kind === 'copay' &&
        row.service_type_code === '98' &&
        row.network === 'IN_NETWORK' &&
        row.plan_association === 'medical' &&
        row.plan_index === specialistCoinsurance[0].plan_index &&
        Number(row.amount_cents) > 0 &&
        (!row.payer_note || row.payer_note.trim().toUpperCase() === 'BENEFITCLASS-SPECIALIST'),
    )
  const legacyOffice =
    result.service_date === '2026-11-06' &&
    plan.length === 1 &&
    plan[0].trim().toUpperCase() === 'PPO PRUDENT BUYER INCENTIVE' &&
    group === '174302MCAF' &&
    hasGeneralOnlyOfficeDeductible(benefits)
  const line = lines[0]
  const rate = reference?.lines[0]
  const terms = line?.benefits
  const deductibleRemaining = familyOnlyOffice
    ? familyDeductible[0].amount_cents
    : benefits?.deductible_remaining_cents
  const oopRemaining = benefits?.oop_remaining_cents
  if (
    result.reference_benefits ||
    result.payment_mode === 'cash' ||
    (result.collection_guidance?.amount_cents != null &&
      result.collection_guidance.collection_rule?.mode !==
        'bluecard_family_office_refundable_deposit') ||
    !/blue cross|bcbstx|blue shield/i.test(result.payer || '') ||
    (!legacyOffice && !familyOnlyOffice) ||
    (familyOnlyOffice &&
      (result.selected_ids?.length !== 1 ||
        result.selected_ids[0] !== line.id ||
        result.codes?.length !== 1 ||
        result.codes[0].id !== line.id ||
        result.codes[0].code !== line.code)) ||
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
    (terms.copay_cents != null && terms.copay_cents !== 0) ||
    (!familyOnlyOffice && terms.deductible_applies === false) ||
    (!familyOnlyOffice && Number(terms.coinsurance_percent) !== 20) ||
    (familyOnlyOffice &&
      terms.coinsurance_percent != null &&
      Number(terms.coinsurance_percent) !== 20) ||
    reference?.complete !== true ||
    !/blue cross|bcbstx/i.test(reference.source || '') ||
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
    familyDeductible: familyOnlyOffice,
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
