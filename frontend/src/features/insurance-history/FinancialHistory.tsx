import { useCallback, useEffect, useState } from 'react'
import { BadgeDollarSign, RefreshCw } from 'lucide-react'
import { getStaffCheckFinancials } from '../../services/api/staff-history'
import { commercialFinancials, type VisitFinancials } from '../../services/api/commercial-estimator'
import { money, visitDate, visitTime } from '../commercial-estimate/format'

export function FinancialHistory({ checkId, intakeId }: { checkId: string; intakeId?: string }) {
  const [data, setData] = useState<VisitFinancials | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const read = useCallback(
    () => (intakeId ? getStaffCheckFinancials(intakeId, checkId) : commercialFinancials(checkId)),
    [checkId, intakeId],
  )
  const load = useCallback(async () => {
    setBusy(true)
    setError('')
    try {
      setData(await read())
    } catch {
      setError('The financial connection could not be read. Retry the check.')
    } finally {
      setBusy(false)
    }
  }, [read])
  useEffect(() => {
    let active = true
    read()
      .then((value) => {
        if (active) setData(value)
      })
      .catch(() => {
        if (active) setError('The financial connection could not be read. Retry the check.')
      })
      .finally(() => {
        if (active) setBusy(false)
      })
    return () => {
      active = false
    }
  }, [read])
  return (
    <section className="financial-history">
      <div className="visit-section-heading">
        <div>
          <h3>
            <BadgeDollarSign size={20} aria-hidden="true" /> Processed claims
          </h3>
        </div>
        <button className="visit-text-button" disabled={busy} onClick={() => void load()}>
          <RefreshCw size={16} /> Refresh
        </button>
      </div>
      <p className="visit-muted">
        Actual amounts from the insurer’s processed claims. They appear when the connected payer
        feed receives the receipt; timing varies.
      </p>
      {busy ? (
        <p role="status">Reading insurance receipts…</p>
      ) : (
        <p role={error ? 'alert' : undefined}>{error || data?.message}</p>
      )}
      {!!data?.lines.length && (
        <div className="clinical-table-wrap">
          <table className="clinical-service-table">
            <thead>
              <tr>
                <th>Service / payer</th>
                <th>Allowed amount</th>
                <th>Insurer paid</th>
                <th>Patient share</th>
              </tr>
            </thead>
            <tbody>
              {data.lines.map((row) => (
                <tr key={`${row.transaction_id}:${row.claim_reference}:${row.line_index}`}>
                  <th scope="row">
                    CPT {row.code}
                    {row.modifiers.length ? ` · ${row.modifiers.join(', ')}` : ''}
                    <small>
                      {row.service_date
                        ? visitDate(row.service_date.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3'))
                        : 'Service date not returned'}{' '}
                      · {row.payer}
                    </small>
                    <small>
                      {(
                        {
                          '1': 'Primary insurance processed',
                          '2': 'Secondary insurance processed',
                          '3': 'Additional insurance processed',
                          '4': 'Denied',
                          '22': 'Payment reversed',
                        } as Record<string, string>
                      )[row.claim_status || ''] || 'Payer response'}{' '}
                      · Received{' '}
                      {row.received_at ? visitDate(row.received_at) : 'date not supplied'}
                    </small>
                  </th>
                  <td>{row.allowed_cents == null ? 'Not returned' : money(row.allowed_cents)}</td>
                  <td>
                    {row.insurer_paid_cents == null
                      ? 'Not returned'
                      : money(row.insurer_paid_cents)}
                  </td>
                  <td>
                    {row.patient_responsibility_cents == null
                      ? 'Not returned'
                      : money(row.patient_responsibility_cents)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && (
        <small>
          {data.source} · Checked {visitDate(data.checked_at)} {visitTime(data.checked_at)}
          {data.partial ? ' · Partial history' : ''}. These records do not show payments collected
          from the patient.
        </small>
      )}
    </section>
  )
}
