import { useState } from 'react'
import { commercialActivateCashSchedule, commercialUploadCashSchedule, type CashScheduleDraft } from '../services/api/commercial-estimator'

export default function PracticeCashSchedule({ refresh }: { refresh: () => Promise<void> }) {
  const [file, setFile] = useState<File | null>(null)
  const [effective, setEffective] = useState('')
  const [draft, setDraft] = useState<CashScheduleDraft | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const upload = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!file) return
    setBusy(true); setMessage(''); setDraft(null)
    try { setDraft(await commercialUploadCashSchedule(file, effective)) }
    catch (error) { setMessage(error instanceof Error ? error.message : 'The schedule could not be read.') }
    finally { setBusy(false) }
  }
  const activate = async () => {
    if (!draft) return
    setBusy(true); setMessage('')
    try { await commercialActivateCashSchedule(draft.id); setDraft(null); setMessage('Cash schedule saved for this practice.'); await refresh() }
    catch (error) { setMessage(error instanceof Error ? error.message : 'The schedule could not be saved.') }
    finally { setBusy(false) }
  }
  return <details className="visit-history"><summary>Manage this practice’s cash prices</summary><p>Upload an approved PDF, Word document or CSV with a table of CPT, Description and Cash Price. Prices apply only to this practice. Cash prices are separate from insurance rates.</p>
    <form className="cash-schedule-upload" onSubmit={upload}><label>Cash price document<input type="file" accept=".pdf,.docx,.csv" required disabled={busy} onChange={event => { setFile(event.target.files?.[0] || null); setDraft(null) }} /></label><label>Effective from<input type="date" required value={effective} disabled={busy} onChange={event => { setEffective(event.target.value); setDraft(null) }} /></label><button className="visit-primary" disabled={busy}>{busy ? 'Reading…' : 'Preview prices'}</button></form>
    {message && <p role="status">{message}</p>}
    {draft && <div><p><strong>{draft.filename}</strong> · {draft.rows.length} prices · Effective {draft.effective_from}</p><div className="clinical-table-wrap cash-schedule-preview"><table className="clinical-service-table"><thead><tr><th>CPT</th><th>Service</th><th>Cash price</th></tr></thead><tbody>{draft.rows.map(row => <tr key={row.code}><td>{row.code}</td><td>{row.description}</td><td>{new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(row.cash_cents / 100)}</td></tr>)}</tbody></table></div><button className="visit-primary" disabled={busy} onClick={() => void activate()}>Use this cash schedule</button></div>}
  </details>
}
