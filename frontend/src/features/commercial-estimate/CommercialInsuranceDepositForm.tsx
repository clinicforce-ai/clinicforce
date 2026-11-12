import type { PrevisitEstimate } from '../../services/api/commercial-estimator'
import type { CommercialEstimateModel } from './useCommercialEstimateModel'

export default function CommercialInsuranceDepositForm({
  result,
  model,
}: {
  result: PrevisitEstimate
  model: CommercialEstimateModel
}) {
  const { readOnly, busy, recovering, saveReview, cash, session } = model
  const lines = result.price_range?.lines || []
  const line = lines[0]
  const eligibleVisit =
    !cash &&
    session?.role === 'admin' &&
    result.payer?.trim().toLowerCase() === 'aetna' &&
    result.plan_details?.stedi?.names?.length === 1 &&
    result.plan_details.stedi.names[0] === 'Aetna Choice POS II' &&
    result.service_selection_status === 'approved' &&
    result.benefits?.active === true &&
    result.benefits?.network_status === 'in_network' &&
    result.price_range?.mode === 'services' &&
    lines.length === 1 &&
    line?.code === '99213' &&
    Number(line.units) === 1 &&
    !line.modifiers?.length &&
    line.allowed_cents == null &&
    result.appointment_context?.place_of_service === '11'
  if (readOnly || cash || session?.role !== 'admin') return null
  if (!eligibleVisit)
    return result.insurance_office_deposit ? (
      <button
        type="button"
        disabled={!!busy || recovering}
        onClick={() => void saveReview({ clear_insurance_office_deposit: true })}
      >
        Remove outdated deposit approval
      </button>
    ) : null

  return (
    <form
      key={result.revision}
      className="visit-cash-quote"
      onSubmit={(event) => {
        event.preventDefault()
        const fields = new FormData(event.currentTarget)
        const amountText = String(fields.get('deposit_amount') || '')
        const amount = Number(amountText)
        const reference = String(fields.get('deposit_reference') || '').trim()
        if (!/^\d+(?:\.\d{1,2})?$/.test(amountText) || !Number.isFinite(amount) || amount <= 0)
          return
        void saveReview({
          insurance_office_deposit: {
            amount_cents: Math.round(amount * 100),
            reference,
            approve_refundable_deposit: true,
          },
        })
      }}
    >
      <p>
        Availity MPPO Open Choice, Office CPT 99213, modifier blank: enter the office fee maximum
        only if the clinic approves it as a refundable visit deposit. It is not the confirmed Aetna
        allowance or patient share.
      </p>
      <label>
        Refundable deposit amount
        <input
          name="deposit_amount"
          type="number"
          min="0.01"
          step="0.01"
          required
          defaultValue={
            result.insurance_office_deposit
              ? result.insurance_office_deposit.amount_cents / 100
              : ''
          }
        />
      </label>
      <label>
        Availity MPPO office lookup reference and date (YYYY-MM-DD)
        <input
          name="deposit_reference"
          type="text"
          minLength={10}
          maxLength={300}
          required
          defaultValue={result.insurance_office_deposit?.reference || ''}
        />
      </label>
      <button type="submit" disabled={!!busy || recovering}>
        Approve refundable deposit for this visit
      </button>
      {result.insurance_office_deposit && (
        <button
          type="button"
          disabled={!!busy || recovering}
          onClick={() => void saveReview({ clear_insurance_office_deposit: true })}
        >
          Remove deposit approval
        </button>
      )}
    </form>
  )
}
