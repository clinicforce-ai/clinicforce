import { useRef, useState } from 'react'
import { Trash2 } from 'lucide-react'
import { deleteStaffInsuranceCheck, getStaffInsuranceChecks, getStaffInsuranceCheck, type InsuranceCheckSnapshot, type SavedInsuranceCheck } from '../services/api/staff'
import CommercialEstimator, { FinancialHistory } from './CommercialEstimator'

export default function SavedInsuranceChecks({ intakeId, patientName, onChange, checksForVisit, currentAccountNumber }: { intakeId: string; patientName: string; onChange: () => Promise<void>; checksForVisit?: SavedInsuranceCheck[]; currentAccountNumber?: string | null }) {
  const [checks, setChecks] = useState<SavedInsuranceCheck[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [cleanupId, setCleanupId] = useState('')
  const [selected, setSelected] = useState<SavedInsuranceCheck | null>(null)
  const [snapshots, setSnapshots] = useState<InsuranceCheckSnapshot[]>([])
  const [revision, setRevision] = useState(0)
  const [loadingCheck, setLoadingCheck] = useState(false)
  const [checkError, setCheckError] = useState('')
  const [showClaims, setShowClaims] = useState(false)
  const requestId = useRef(0)
  async function open(check: SavedInsuranceCheck) {
    const id = ++requestId.current
    setSelected(check); setShowClaims(false); setSnapshots([]); setRevision(0); setLoadingCheck(true); setCheckError('')
    try {
      const result = await getStaffInsuranceCheck(check.intake_id || intakeId, check.id)
      if (id === requestId.current) setSnapshots(result.snapshots)
    } catch (caught) {
      if (id === requestId.current) setCheckError(caught instanceof Error ? caught.message : 'The saved estimate could not be loaded.')
    } finally { if (id === requestId.current) setLoadingCheck(false) }
  }
  async function load() {
    setBusy(true); setError('')
    try {
      const result = checksForVisit || (await getStaffInsuranceChecks(intakeId)).checks
      setChecks(result)
      const latest = result.find(check => check.source_mode === 'commercial_previsit')
      if (latest) void open(latest)
    }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Saved checks could not be loaded.') }
    finally { setBusy(false) }
  }
  async function remove(check: SavedInsuranceCheck) {
    if (!cleanupId && !window.confirm(`Delete ${check.label.toLowerCase()} for ${patientName}, saved ${new Date(check.created_at).toLocaleString()}? This removes this check and its associated files. The patient and other checks stay unchanged. This cannot be undone.`)) return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await deleteStaffInsuranceCheck(check.intake_id || intakeId, check.id)
      if (result.cleanup_pending) {
        setCleanupId(check.id); setError('The check was removed. Some files still need cleanup. Select Retry file cleanup to finish.')
      } else {
        setCleanupId(''); setChecks(current => current?.filter(row => row.id !== check.id) || [])
        if (selected?.id === check.id) { requestId.current++; setSelected(null); setSnapshots([]) }
        setNotice('Insurance check deleted.'); await onChange()
      }
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'The check could not be deleted.') }
    finally { setBusy(false) }
  }
  return <details className="staff-panel staff-saved-checks" onToggle={event => { if (event.currentTarget.open && checks === null && !busy && !error) void load() }}>
    <summary>{checksForVisit ? 'View saved estimate results' : 'Saved insurance checks'}</summary>
    <p className="staff-muted">{checksForVisit ? 'Recommendations recorded for this appointment, with the benefits, services, and assumptions used at the time.' : 'Patient history across visits and confirmed intakes. Open a visit to review the saved recommendation, benefits, services, and assumptions.'}</p>
    {notice && <p role="status">{notice}</p>}
    {error && <p className="staff-alert staff-error" role="alert">{error}{checks === null && <button disabled={busy} onClick={() => void load()}>Try again</button>}</p>}
    {busy && <p role="status">Updating saved checks…</p>}
    {checks?.length === 0 && <p className="staff-muted">No saved insurance checks for this patient.</p>}
    {checks?.map(check => <div className="staff-document" key={check.id}><span>{check.source_mode === 'commercial_previsit' ? <button className="staff-text-button" aria-pressed={selected?.id === check.id} onClick={() => void open(check)}><strong>{check.service_date || 'Visit'} · {check.visit_type === 'established' ? 'Established / follow-up patient' : check.visit_type === 'new' ? 'New patient' : check.label}</strong></button> : <strong>{check.label}</strong>}<small>Check created {new Date(check.created_at).toLocaleString()}{check.pending ? ' · Payer request unresolved' : ''}</small></span><button className="staff-delete-button" disabled={busy || check.pending || (!!cleanupId && cleanupId !== check.id)} onClick={() => void remove(check)}><Trash2 size={16} aria-hidden="true" />{cleanupId === check.id ? 'Retry file cleanup' : 'Delete check'}</button></div>)}
    {selected && <section aria-label="Saved visit record">
      {loadingCheck && <p role="status">Loading the saved estimate…</p>}
      {checkError && <p className="staff-alert staff-error" role="alert">{checkError} <button onClick={() => void open(selected)}>Retry</button></p>}
      {snapshots.length > 1 && <label className="staff-field"><span>Recorded versions</span><select value={revision} onChange={event => setRevision(Number(event.target.value))}>{snapshots.map((snapshot, index) => <option key={index} value={index}>{snapshot.saved_at ? new Date(snapshot.saved_at).toLocaleString() : 'Earlier saved result'} · Revision {snapshot.result.revision} · {snapshot.stage.replaceAll('-', ' ')}</option>)}</select></label>}
      {snapshots[revision] && <CommercialEstimator key={`${selected.id}:${revision}`} intakeId={selected.intake_id || intakeId} saved={snapshots[revision]} currentAccountNumber={currentAccountNumber} />}
      {!!snapshots.length && <details className="staff-panel" onToggle={event => setShowClaims(event.currentTarget.open)}><summary>Compare with processed claims</summary><p className="staff-muted">Compare the saved patient estimate above with the insurer’s actual patient share below. These claim records are retrieved now; the saved recommendation stays unchanged.</p>{showClaims && <FinancialHistory key={selected.id} checkId={selected.id} intakeId={selected.intake_id || intakeId} />}</details>}
    </section>}
  </details>
}
