import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'

export default function CommercialCashQuoteForm({
  result,
  model,
}: {
  result: PrevisitEstimate
  model: CommercialEstimateModel
}) {
  const { readOnly, busy, recovering, saveReview, cash } = model
  const lines = result.price_range?.lines || []
  const canQuoteFollowUp =
    cash &&
    result.service_selection_status === 'approved' &&
    result.price_range?.mode === 'services' &&
    lines.length === 1 &&
    lines[0].code === '99213' &&
    Number(lines[0].units) === 1 &&
    !lines[0].modifiers?.length
  if (readOnly || !canQuoteFollowUp) return null
  return (
    <form
      key={result.revision}
      className="visit-cash-quote"
      onSubmit={(event) => {
        event.preventDefault()
        const fields = new FormData(event.currentTarget)
        const amountText = String(fields.get('cash_amount') || '')
        const amount = Number(amountText)
        const reason = String(fields.get('cash_reason') || '').trim()
        if (
          !/^\d+(?:\.\d{1,2})?$/.test(amountText) ||
          !Number.isFinite(amount) ||
          amount <= 0 ||
          !reason
        )
          return
        void saveReview({
          payment_mode: 'cash',
          cash_visit_quote: {
            amount_cents: Math.round(amount * 100),
            reason,
          },
        })
      }}
    >
      <label>
        Follow-up cash price
        <input
          name="cash_amount"
          type="number"
          min="0.01"
          step="0.01"
          required
          defaultValue={result.cash_quote?.reason ? (result.cash_quote.min_cents || 0) / 100 : ''}
        />
      </label>
      <label>
        Reason
        <input
          name="cash_reason"
          type="text"
          minLength={3}
          maxLength={200}
          required
          defaultValue={
            result.cash_quote?.reason ||
            (result.benefits?.active === false ? 'inactive general medical coverage' : '')
          }
        />
      </label>
      <button type="submit" disabled={!!busy || recovering}>
        Save visit cash quote
      </button>
      {result.cash_quote?.reason && (
        <button
          type="button"
          disabled={!!busy || recovering}
          onClick={() => void saveReview({ clear_cash_visit_quote: true })}
        >
          Remove quote
        </button>
      )}
    </form>
  )
}
