import { useState } from 'react'
import type { PrevisitEstimate, VisitReview } from '../../services/api/commercial-estimator'
import { money } from './service-code'

export function PayerAllowanceEntry({
  result,
  busy,
  save,
  viewEstimate,
}: {
  result: PrevisitEstimate
  busy: boolean
  save: (review: VisitReview) => Promise<boolean>
  viewEstimate: () => void
}) {
  const selected = result.codes.filter((row) => row.id && result.selected_ids?.includes(row.id))
  const [lineId, setLineId] = useState(selected[0]?.id || '')
  const [dollars, setDollars] = useState('')
  const [channel, setChannel] = useState<'payer_portal' | 'payer_phone' | 'payer_document'>(
    'payer_portal',
  )
  const [reference, setReference] = useState('')
  const [validThrough, setValidThrough] = useState(result.service_date)
  const [error, setError] = useState('')
  if (!selected.length) return null
  const recorded = result.payer_allowances?.[lineId]
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    const normalized = dollars.trim()
    const cents = /^\d+(?:\.\d{1,2})?$/.test(normalized)
      ? Math.round(Number(normalized) * 100)
      : NaN
    if (!Number.isSafeInteger(cents) || cents <= 0 || cents > 100_000_000) {
      setError('Enter the payer allowed amount per unit in dollars and cents.')
      return
    }
    if (reference.trim().length < 8 || !validThrough || validThrough < result.service_date) {
      setError('Enter a payer reference and a quote date covering this visit.')
      return
    }
    setError('')
    if (
      await save({
        payer_allowance: {
          line_id: lineId,
          unit_allowed_cents: cents,
          channel,
          reference: reference.trim(),
          valid_through: validThrough,
        },
      })
    )
      viewEstimate()
  }
  return (
    <section aria-label="Payer out-of-network allowance">
      <h4>Payer out-of-network allowance</h4>
      <p>
        Enter only the per-unit allowed amount UHC gave for this patient, CPT, provider, location
        and visit date. The estimate uses this quote with the returned benefits; it is not a clinic
        fee schedule.
      </p>
      <form onSubmit={submit}>
        <label>
          Selected CPT{' '}
          <select value={lineId} onChange={(event) => setLineId(event.target.value)}>
            {selected.map((row) => (
              <option key={row.id} value={row.id}>
                CPT {row.code} × {row.units}
              </option>
            ))}
          </select>
        </label>
        <label>
          Allowed per unit ($){' '}
          <input
            type="text"
            inputMode="decimal"
            value={dollars}
            onChange={(event) => setDollars(event.target.value)}
            required
          />
        </label>
        <label>
          Payer source{' '}
          <select
            value={channel}
            onChange={(event) => setChannel(event.target.value as typeof channel)}
          >
            <option value="payer_portal">Payer portal</option>
            <option value="payer_phone">Payer phone representative</option>
            <option value="payer_document">Payer document</option>
          </select>
        </label>
        <label>
          Payer quote reference{' '}
          <input
            value={reference}
            onChange={(event) => setReference(event.target.value)}
            minLength={8}
            required
          />
        </label>
        <label>
          Valid through{' '}
          <input
            type="date"
            value={validThrough}
            min={result.service_date}
            onChange={(event) => setValidThrough(event.target.value)}
            required
          />
        </label>
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={busy}>
          Save payer allowance
        </button>
      </form>
      {recorded && (
        <p>
          Recorded: {money(recorded.unit_allowed_cents ?? 0)} per unit from{' '}
          {recorded.channel?.replaceAll('_', ' ')}; reference {recorded.reference}; valid through{' '}
          {recorded.valid_through}.{' '}
          <button
            type="button"
            disabled={busy}
            onClick={async () => {
              if (await save({ clear_payer_allowance_line_id: lineId })) viewEstimate()
            }}
          >
            Remove quote
          </button>
        </p>
      )}
    </section>
  )
}
