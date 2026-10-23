import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { money } from './format'

function nextStep(model: CommercialEstimateModel, result: PrevisitEstimate) {
  const lines = result.price_range?.mode === 'services' ? result.price_range.lines || [] : []
  if (!lines.length && !result.selected_ids?.length)
    return { title: 'Select services', hint: 'Choose the planned CPTs for this visit.' }
  if (
    lines.some((line) => line.rate_resolution?.status !== 'RESOLVED') ||
    !result.price_range?.complete
  )
    return { title: 'Check CPT prices', hint: 'Match payer rates to the selected services.' }
  if (result.benefits?.active !== true)
    return { title: 'Check coverage', hint: 'Run eligibility for this visit.' }
  if (result.benefits.has_secondary || result.recovery?.reason_code === 'multiple_active_policies')
    return { title: 'Confirm payer order', hint: 'Resolve the active policies before collecting.' }
  if (model.guidance?.policy_setup_required)
    return {
      title: 'Approve collection policy',
      hint: 'The clinic must approve this payer route’s advance rule.',
    }
  if (model.requirementIssue)
    return {
      title: 'Review requirements',
      hint: 'Check referral and authorization for the selected CPTs.',
    }
  if (!/^in network|^out of network/i.test(model.networkLabel || ''))
    return { title: 'Verify network', hint: 'Confirm this plan and provider’s network tier.' }
  return { title: 'Review estimate', hint: 'See the missing item in the table below.' }
}

export default function ArrivalAmountSummary({
  model,
  result,
  partialCopay,
  visibleAmount,
}: {
  model: CommercialEstimateModel
  result: PrevisitEstimate
  partialCopay: number | null
  visibleAmount: boolean
}) {
  const action = nextStep(model, result)
  const needsAction = model.amountText === 'Insured amount unavailable'
  const additionalServicesPending = result.price_range?.lines?.some(
    (line) => !/^992(?:0[2-5]|1[2-5])$/.test(line.code),
  )
  return (
    <>
      <span>
        {partialCopay != null
          ? 'Payer-reported office copay · collection pending'
          : needsAction
            ? 'Next step'
            : model.amountLabel}
      </span>
      <strong className={!visibleAmount ? 'amount-pending' : undefined} role="status">
        {partialCopay != null ? money(partialCopay) : needsAction ? action.title : model.amountText}
      </strong>
      {partialCopay != null ? (
        <p>
          {additionalServicesPending
            ? 'Office copay reported; other service costs pending.'
            : 'Office copay reported; collection pending.'}
        </p>
      ) : needsAction ? (
        <p>{action.hint}</p>
      ) : model.previousEstimate ? (
        <p>Recheck this saved visit before collecting.</p>
      ) : model.provisionalAmount != null || model.estimatedShare ? (
        <p>Planning estimate only. See the collection decision below.</p>
      ) : model.guidance?.basis === 'claim_after_service' && model.amount ? (
        <p>Claim first. Patient responsibility follows the processed claim.</p>
      ) : model.guidance?.basis === 'office_copay' && model.amount ? (
        <p>Office copay advance; other service costs may follow the claim.</p>
      ) : null}
    </>
  )
}
