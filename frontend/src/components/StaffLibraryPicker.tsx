import { useEffect, useId, useState } from 'react'
import { detailFrom, request } from '../services/api/client'

type Entry = { id: string; kind: string; filename: string; label: string; payer?: string; effective_from?: string; effective_to?: string }

export default function StaffLibraryPicker({ kinds, disabled, onFile }: { kinds: string[]; disabled: boolean; onFile: (kind: string, file: File) => void }) {
  const id = useId()
  const [documents, setDocuments] = useState<Entry[]>([])
  const [selected, setSelected] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let canceled = false
    request<{ documents: Entry[] }>('/api/staff/library').then(result => { if (!canceled) setDocuments(result.documents) })
      .catch((caught: unknown) => { if (!canceled) setError(caught instanceof Error ? caught.message : 'Unable to load clinic files.') })
    return () => { canceled = true }
  }, [])
  const applicable = documents.filter(document =>
    (kinds.includes(document.kind) || (kinds.includes('fees') && document.kind === 'fee_schedule')) &&
    (document.kind === 'fee_schedule' ? /\.csv$/i.test(document.filename) :
      document.kind === 'contract' ? /\.(pdf|png|jpe?g)$/i.test(document.filename) : /\.(csv|pdf|png|jpe?g)$/i.test(document.filename)))
  const choose = async () => {
    const document = applicable.find(item => item.id === selected)
    if (!document) return
    setBusy(true); setError('')
    try {
      const response = await fetch(`/api/staff/library/${encodeURIComponent(document.id)}/document`, { credentials: 'same-origin' })
      if (!response.ok) throw new Error(detailFrom(await response.json().catch(() => null), 'Unable to open the clinic file.'))
      const blob = await response.blob()
      onFile(kinds.includes('fees') ? 'fees' : document.kind, new File([blob], document.filename, { type: blob.type }))
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Unable to use this clinic file.') }
    finally { setBusy(false) }
  }
  return <details className="source-excerpt" style={{ marginBottom: 20 }}>
    <summary>{kinds.includes('fees') && !kinds.includes('contract') ? 'Use a saved clinic fee schedule' : 'Use a saved clinic agreement or fee file'}</summary>
    <p>These are clinic-level files, saved once and reused across appointments. Choose the version that applies to this payer, provider and visit date. Availity returns payer benefits; it does not provide the clinic’s fee schedule or payer contract.</p>
    <label htmlFor={id}>Clinic file</label>
    <select id={id} value={selected} onChange={event => setSelected(event.target.value)} disabled={disabled || busy}>
      <option value="">{applicable.length ? 'Choose an applicable file' : 'No matching clinic files saved'}</option>
      {applicable.map(document => <option value={document.id} key={document.id}>{document.label || document.filename} · {document.payer || 'Review payer'} · {document.effective_from || 'Start unspecified'} to {document.effective_to || 'End unspecified'}</option>)}
    </select>
    <button type="button" className="button button-secondary" disabled={disabled || busy || !selected} onClick={() => void choose()}>{busy ? 'Opening file…' : 'Use selected file'}</button>
    {error && <p className="form-error" role="alert">{error}</p>}
  </details>
}
