import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { patientBenefitsBound } from './identityBinding'

export function nextStep(model: CommercialEstimateModel, result: PrevisitEstimate) {
  const lines = result.price_range?.mode === 'services' ? result.price_range.lines || [] : []
  if (result.benefits?.active !== true)
    return {
      title: 'Check coverage',
      hint: 'Run eligibility for this visit before setting a collection amount.',
    }
  if (!lines.length && result.service_plan?.method === 'unavailable')
    return {
      title: 'CPT suggestion unavailable',
      hint: 'Retry the coding suggestion for this visit.',
    }
  if (!lines.length && !result.selected_ids?.length)
    return { title: 'Select services', hint: 'Choose the planned CPTs for this visit.' }
  if (
    (result.benefits.has_secondary ||
      result.recovery?.reason_code === 'multiple_active_policies') &&
    result.benefits.primary_secondary_order?.status !== 'verified'
  )
    return { title: 'Confirm payer order', hint: 'Resolve the active policies before collecting.' }
  if (result.benefits.source_errors?.length)
    return {
      title: 'Check payer response',
      hint: 'Review the incomplete benefit check for this visit.',
    }
  if (result.identity && !patientBenefitsBound(result.identity))
    return {
      title: 'Reconcile patient and policy',
      hint: 'The payer details are not bound to this patient and policy. Use the clinic’s claim-after-service policy before setting a charge.',
    }
  if (result.benefits.service_date_matches === false)
    return {
      title: 'Choose check-in policy',
      hint: 'The payer returned benefits for an earlier date. Use an approved clinic advance or $0 claim-after-service policy for this visit.',
    }
  const cignaTerms = [result.benefits, ...lines.map((line) => line.benefits)]
  if (
    /^Cigna$/i.test(result.payer || '') &&
    result.collection_guidance?.amount_cents == null &&
    cignaTerms.every(
      (term) =>
        term?.copay_cents == null &&
        term?.deductible_annual_cents == null &&
        term?.deductible_remaining_cents == null &&
        term?.coinsurance_percent == null &&
        term?.oop_remaining_cents == null,
    )
  )
    return {
      title: 'Check Cigna plan benefits',
      hint:
        result.appointment_context?.place_of_service === '11'
          ? 'Request visit-date office cost sharing and provider participation from the plan administrator.'
          : 'Request visit-date cost sharing and provider participation from the plan administrator.',
    }
  if (!/^in network|^out of network/i.test(model.networkLabel || ''))
    return { title: 'Verify network', hint: 'Confirm this plan and provider’s network tier.' }
  if (model.requirementIssue)
    return {
      title: 'Review requirements',
      hint: 'Check referral and authorization for the selected CPTs.',
    }
  if (
    result.collection_guidance?.positive_advance_policy_missing ||
    result.collection_guidance?.published_copay_policy_missing
  )
    return {
      title: 'Approve collection policy',
      hint: 'The clinic must approve this payer route’s advance rule.',
    }
  if (
    result.benefit_source?.includes('plan administrator') &&
    lines.length === 1 &&
    /^992(?:0[2-5]|1[2-5])$/.test(lines[0].code) &&
    lines[0].benefits?.copay_cents == null
  )
    return {
      title: 'Review office copay',
      hint: 'Confirm the diagnosis and applicable specialist copay.',
    }
  if (lines.some((line) => line.rate_resolution?.status !== 'RESOLVED'))
    return { title: 'Check CPT prices', hint: 'Match payer rates to the selected services.' }
  return { title: 'Review estimate', hint: 'See the missing item in the table below.' }
}
