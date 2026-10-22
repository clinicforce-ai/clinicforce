import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'
import { money } from './format'
import { overviewExplanation } from './overviewExplanation'

export default function ArrivalAmountSummary({
  model,
  result,
  partialCopay,
  visibleAmount,
  fullBreakdown,
}: {
  model: CommercialEstimateModel
  result: PrevisitEstimate
  partialCopay: number | null
  visibleAmount: boolean
  fullBreakdown: boolean
}) {
  const emgPending = result.price_range?.lines?.some((line) =>
    ['95908', '95886'].includes(line.code),
  )
  return (
    <>
      <span>
        {partialCopay != null
          ? 'Payer-reported office copay · collection pending'
          : model.amountLabel}
      </span>
      <strong className={!visibleAmount ? 'amount-pending' : undefined} role="status">
        {partialCopay != null ? money(partialCopay) : model.amountText}
      </strong>
      <p>
        {partialCopay != null
          ? `The payer reports this office copay. ${emgPending ? 'The EMG balance remains pending the claim. ' : ''}This is not yet approved for collection.`
          : overviewExplanation(model, result, fullBreakdown)}
      </p>
    </>
  )
}
