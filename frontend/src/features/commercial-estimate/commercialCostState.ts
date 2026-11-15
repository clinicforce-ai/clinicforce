import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CostBreakdownRow } from '../insurance-estimate/CostBreakdownTable'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { money } from './format'

export function publishedGehaZero(result: PrevisitEstimate) {
  return (
    result.collection_guidance?.basis === 'published_geha_pshb_zero_office' &&
    result.collection_guidance.amount_cents === 0
  )
}

export function calculatedShare(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
  automaticCashFallback: boolean,
) {
  return (
    !automaticCashFallback &&
    !model.previousEstimate &&
    !result.encounter_summary?.integrity_error &&
    result.price_range?.complete === true &&
    (result.encounter_summary?.complete === true || model.supportedAmount === true)
  )
}

export function supportedOfficeCopayShare(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
) {
  const guidance = result.collection_guidance
  return model.supportedAmount &&
    guidance?.basis === 'office_copay' &&
    guidance.amount_cents != null
    ? `${money(guidance.amount_cents)} office copay advance; final share pending`
    : null
}

export function pendingHumanaSpecialistCopay(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
): number | null {
  const price = result.price_range
  const line = price?.mode === 'services' && price.lines?.length === 1 ? price.lines[0] : null
  const terms = line?.benefits
  const plan = result.plan_details?.stedi || result.benefits?.plan_details
  const identity = result.identity || result.benefits?.identity
  return !model.cash &&
    !model.previousEstimate &&
    result.collection_guidance?.amount_cents == null &&
    result.billing_protection?.status !== 'qmb' &&
    /\bhumana\b/i.test(result.payer || '') &&
    Boolean(plan?.medicare_ppo_evidence) &&
    ['matched', 'reconciled'].includes(identity?.status || '') &&
    result.benefits?.active === true &&
    !result.benefits.has_secondary &&
    !result.benefits.network_conflict &&
    !result.benefits.coverage_status_conflict &&
    !result.benefits.source_errors?.length &&
    result.benefits.service_date_matches !== false &&
    result.benefits.network_status === 'in_network' &&
    result.benefits.network_source === 'clinic_library_contract' &&
    !price?.service_issues?.length &&
    result.card_check?.status !== 'conflict' &&
    line != null &&
    /^992(?:0[2-5]|1[2-5])$/.test(line.code) &&
    line.units === 1 &&
    line.allowed_basis === 'payer_fee_schedule' &&
    line.rate_resolution?.status === 'RESOLVED' &&
    (line.allowed_cents || 0) > 0 &&
    terms?.active === true &&
    terms.copay_period === 'VISIT' &&
    terms.coinsurance_percent != null &&
    Number(terms.coinsurance_percent) === 0 &&
    !terms.source_errors?.length &&
    typeof terms.copay_cents === 'number' &&
    terms.copay_cents > 0
    ? Math.min(
        terms.copay_cents,
        line.allowed_cents!,
        terms.oop_remaining_cents ?? terms.copay_cents,
      )
    : null
}

export function pendingHumanaPatientShare(
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
): string | null {
  const copay = pendingHumanaSpecialistCopay(result, model)
  return (
    model.estimatedShare ||
    (copay == null
      ? null
      : `${money(copay)} payer-reported specialist office copay; full patient share unresolved`)
  )
}

export function calculatedCoinsurance(
  amountCents: number,
  percent: string | number | null | undefined,
) {
  return `${percent != null ? `${Number(percent).toLocaleString()}% · ` : ''}${money(amountCents)}`
}

export function visibleRateReference(result: PrevisitEstimate) {
  const temporaryRateReference =
    /\bhumana\b/i.test(result.payer || '') &&
    /blue cross/i.test(result.temporary_rate_reference?.source || '')
      ? null
      : result.temporary_rate_reference
  return {
    temporaryRateReference,
    aetnaRateReference: Boolean(
      result.price_range?.lines?.some(
        (line) =>
          line.allowed_basis === 'aetna_fee_schedule' &&
          line.rate_resolution?.status === 'RESOLVED',
      ),
    ),
  }
}

export function applyCostRowCopy(
  rows: CostBreakdownRow[],
  result: PrevisitEstimate,
  model: CommercialEstimateModel,
) {
  if (pendingHumanaSpecialistCopay(result, model) != null) {
    const copayRow = rows.find((row) => row.label === 'Copay')
    if (copayRow)
      copayRow.detail = 'Stedi reports this specialist office copay for the selected visit date.'
    const coinsuranceRow = rows.find((row) => row.label === 'Coinsurance')
    if (coinsuranceRow) coinsuranceRow.value = '0% specialist office coinsurance'
  }
  applyBluePpoFamilyQuote(rows, result)
}

export function applyBluePpoFamilyQuote(rows: CostBreakdownRow[], result: PrevisitEstimate) {
  const guidance = result.collection_guidance
  const mode = guidance?.collection_rule?.mode
  if (
    !guidance ||
    !['blue_ppo_family_deductible_quote', 'cigna_ppo_deductible_quote'].includes(mode || '')
  )
    return
  const share = rows.find((row) => row.label.endsWith('patient share'))
  if (!share) return
  share.value = `${money(guidance.reference_total_cents!)} provisional · ${mode === 'cigna_ppo_deductible_quote' ? 'office deductible assumed' : 'family deductible assumed'}`
  share.detail = guidance.reason
}
