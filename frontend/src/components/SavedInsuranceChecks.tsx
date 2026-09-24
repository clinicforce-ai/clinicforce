import { useState } from 'react'
import { Trash2 } from 'lucide-react'
import { deleteStaffInsuranceCheck, getStaffInsuranceChecks, type SavedInsuranceCheck } from '../services/api/staff'

export default function SavedInsuranceChecks({ intakeId, patientName, onChange }: { intakeId: string; patientName: string; onChange: () => Promise<void> }) {
  const [checks, setChecks] = useState<SavedInsuranceCheck[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [cleanupId, setCleanupId] = useState('')
  async function load() {
    setBusy(true); setError('')
    try { setChecks((await getStaffInsuranceChecks(intakeId)).checks) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Saved checks could not be loaded.') }
    finally { setBusy(false) }
  }
  async function remove(check: SavedInsuranceCheck) {
    if (!cleanupId && !window.confirm(`Delete ${check.label.toLowerCase()} for ${patientName}, saved ${new Date(check.created_at).toLocaleString()}? This removes this check and its associated files. The patient and other checks stay unchanged. This cannot be undone.`)) return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await deleteStaffInsuranceCheck(intakeId, check.id)
      if (result.cleanup_pending) {
        setCleanupId(check.id); setError('The check was removed. Some files still need cleanup. Select Retry file cleanup to finish.')
      } else {
        setCleanupId(''); setChecks(current => current?.filter(row => row.id !== check.id) || [])
        setNotice('Insurance check deleted.'); await onChange()
      }
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'The check could not be deleted.') }
    finally { setBusy(false) }
  }
  return <details className="staff-panel" onToggle={event => { if (event.currentTarget.open && checks === null && !busy && !error) void load() }}>
    <summary>Saved insurance checks</summary>
    {notice && <p role="status">{notice}</p>}
    {error && <p className="staff-alert staff-error" role="alert">{error}{checks === null && <button disabled={busy} onClick={() => void load()}>Try again</button>}</p>}
    {busy && <p role="status">Updating saved checks…</p>}
    {checks?.length === 0 && <p className="staff-muted">No saved insurance checks for this intake.</p>}
    {checks?.map(check => <div className="staff-document" key={check.id}><span><strong>{check.label}</strong><small>{new Date(check.created_at).toLocaleString()}{check.pending ? ' · Payer request unresolved' : ''}</small></span><button className="staff-delete-button" disabled={busy || check.pending || (!!cleanupId && cleanupId !== check.id)} onClick={() => void remove(check)}><Trash2 size={16} aria-hidden="true" />{cleanupId === check.id ? 'Retry file cleanup' : 'Delete check'}</button></div>)}
  </details>
}
