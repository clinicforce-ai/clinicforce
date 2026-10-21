import { useState } from 'react'
import type { PrevisitEstimate, VisitReview } from '../../services/api/commercial-estimator'

export default function BillingProtectionEntry({
  result,
  busy,
  save,
}: {
  result: PrevisitEstimate
  busy: boolean
  save: (review: VisitReview) => Promise<boolean>
}) {
  const [status, setStatus] = useState<'qmb' | 'not_qmb'>('qmb')
  const [reference, setReference] = useState('')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState('')
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (reference.trim().length < 8 || notes.trim().length < 10) {
      setError('Record the source reference and what it confirmed.')
      return
    }
    setError('')
    await save({ billing_protection: { status, reference: reference.trim(), notes: notes.trim() } })
  }
  return (
    <section aria-label="Medicare billing protection">
      <h4>Medicare billing protection</h4>
      <p>
        Verify this member’s QMB status using a current payer or Medicaid source before collecting
        Medicare Advantage cost sharing. A missing status does not authorize an advance.
      </p>
      {result.billing_protection?.verified_at && (
        <p>
          Recorded: {result.billing_protection.status === 'qmb' ? 'QMB protected' : 'Not QMB'} ·{' '}
          {result.billing_protection.reference}
        </p>
      )}
      <form onSubmit={submit}>
        <label>
          Verified status{' '}
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value as typeof status)}
          >
            <option value="qmb">QMB protected</option>
            <option value="not_qmb">Not QMB</option>
          </select>
        </label>
        <label>
          Payer or Medicaid reference{' '}
          <input
            value={reference}
            onChange={(event) => setReference(event.target.value)}
            required
          />
        </label>
        <label>
          Verification notes{' '}
          <input value={notes} onChange={(event) => setNotes(event.target.value)} required />
        </label>
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={busy}>
          Save QMB verification
        </button>
      </form>
    </section>
  )
}
