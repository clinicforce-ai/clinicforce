import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ChevronLeft, ChevronRight, Mail, Plus, RefreshCw, Trash2, Users } from 'lucide-react'
import { deleteStaffIntake, deleteStaffPatient, getClinicalPatients, retryStaffDeletionCleanup, type ClinicalDashboard, type ClinicalPatient } from '../services/api/staff'

interface Props { onOpenIntake: (id: string) => void; onRequestCard: (patient?: ClinicalPatient) => void; onAddCard: () => void; onPullEcw: () => void }
type Cohort = 'all' | 'accounts_with_cards' | 'accounts_without_cards' | 'card_only' | 'other'
const PAGE_SIZE = 7
const name = (patient: ClinicalPatient) => `${patient.given_name} ${patient.family_name}`.trim() || 'Unconfirmed card upload'
const accountLabel = (value: ClinicalPatient['account_status']) => value === 'portal' ? 'Shared patient account' : value === 'automatic' ? 'Insurance intake record' : 'Clinic patient record'
const stateLabel = (value: string) => ({ needs_confirmation: 'Confirm patient', ready: 'Ready for review', in_progress: 'Review in progress', reviewed: 'Staff review complete' } as Record<string, string>)[value] || value.replaceAll('_', ' ')
const cohortLabel: Record<Cohort, string> = { all: 'All patients', accounts_with_cards: 'Accounts with cards', accounts_without_cards: 'Accounts missing cards', card_only: 'Card-only records', other: 'Other clinic patients' }
export default function ClinicalPatientDashboard({ onOpenIntake, onRequestCard, onAddCard, onPullEcw }: Props) {
  const [dashboard, setDashboard] = useState<ClinicalDashboard | null>(null)
  const [cohort, setCohort] = useState<Cohort>('all')
  const [query, setQuery] = useState('')
  const [cardStatus, setCardStatus] = useState('')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<ClinicalPatient | null>(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [cleanupPending, setCleanupPending] = useState(false)
  const [notice, setNotice] = useState('')
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { heading.current?.focus() }, [selected])
  useEffect(() => { let active = true; getClinicalPatients().then(result => { if (active) setDashboard(result) }).catch(caught => { if (active) setError(caught instanceof Error ? caught.message : 'Patients could not be loaded.') }).finally(() => { if (active) setBusy(false) }); return () => { active = false } }, [])
  async function refresh() { setBusy(true); setError(''); try { setDashboard(await getClinicalPatients()) } catch (caught) { setError(caught instanceof Error ? caught.message : 'Patients could not be loaded.') } finally { setBusy(false) } }
  async function deletePatient() {
    if (!selected || (!selected.clinic_patient_id && !selected.latest_intake_id) || deleting) return
    if (!cleanupPending && !window.confirm(`Delete ${name(selected)} from this clinic’s ClinicForce workspace? Their clinic intakes, insurance checks, and associated files will be removed. eCW records and their own patient account stay unchanged. This cannot be undone.`)) return
    setDeleting(true); setError('')
    try {
      const result = await (selected.clinic_patient_id ? deleteStaffPatient(selected.clinic_patient_id) : deleteStaffIntake(selected.latest_intake_id!))
      if (result.cleanup_pending) {
        setCleanupPending(true)
        setError('The clinic record was removed. Some stored files still need cleanup. Select Retry file cleanup to finish.')
      } else {
        setSelected(null); setCleanupPending(false); setNotice('Patient and linked insurance checks deleted from this clinic.')
        await refresh()
      }
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'The patient could not be deleted.') }
    finally { setDeleting(false) }
  }
  async function retryCleanup() {
    setDeleting(true); setError(''); setNotice('')
    try {
      const result = await retryStaffDeletionCleanup()
      await refresh()
      if (result.pending_file_cleanup) setError('Some files could not be removed. Retry file cleanup when storage is available.')
      else setNotice('Deleted records’ file cleanup is complete.')
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'File cleanup could not be completed.') }
    finally { setDeleting(false) }
  }
  function inCohort(patient: ClinicalPatient) {
    if (cohort === 'all') return true
    if (cohort === 'accounts_with_cards') return patient.account_status === 'portal' && patient.card_status === 'received'
    if (cohort === 'accounts_without_cards') return patient.account_status === 'portal' && patient.card_status === 'missing'
    if (cohort === 'card_only') return patient.account_status !== 'portal' && patient.card_status === 'received'
    return patient.account_status !== 'portal' && patient.card_status === 'missing'
  }
  const patients = dashboard?.patients.filter(patient => inCohort(patient) && (!cardStatus || patient.card_status === cardStatus) && `${name(patient)} ${patient.email || ''} ${patient.birth_date}`.toLowerCase().includes(query.trim().toLowerCase())) || []
  const pageCount = Math.max(1, Math.ceil(patients.length / PAGE_SIZE))
  const currentPage = Math.min(page, pageCount)
  const pageStart = (currentPage - 1) * PAGE_SIZE
  const visiblePatients = patients.slice(pageStart, pageStart + PAGE_SIZE)
  if (selected) return <div className="staff-narrow"><button className="staff-text-button" disabled={deleting} onClick={() => { setSelected(null); setError(''); setCleanupPending(false); void refresh() }}><ArrowLeft size={18} /> Clinical patients</button><header className="staff-header"><div><div className="staff-eyebrow">Clinical patient record</div><h1 ref={heading} tabIndex={-1}>{name(selected)}</h1><p className="staff-muted">Date of birth: {selected.birth_date || 'Needs confirmation'} · {accountLabel(selected.account_status)}</p></div><div className="staff-actions">{(selected.clinic_patient_id || selected.latest_intake_id) && <button className="staff-text-button staff-delete-button" disabled={deleting} onClick={() => void deletePatient()}><Trash2 size={17} aria-hidden="true" />{deleting ? 'Deleting…' : cleanupPending ? 'Retry file cleanup' : selected.clinic_patient_id ? 'Delete patient' : 'Delete unconfirmed upload'}</button>}<button className="staff-primary" disabled={deleting || cleanupPending} onClick={() => onRequestCard(selected)}><Mail size={18} /> Request insurance card</button></div></header>{error && <p className="staff-alert staff-error" role="alert">{error}</p>}<section className="staff-panel"><h2>Insurance submissions and history</h2><p className="staff-muted">Open the current insurance review. Previous submissions and checks remain in the history below.</p>{selected.email && <p className="staff-small staff-muted">Contact email: {selected.email}</p>}{!selected.intakes.length ? <p className="staff-muted">No insurance submissions yet. Request a card to get started.</p> : selected.intakes.map(intake => <div className="staff-document" key={intake.id}><span><span><strong>{intake.source === 'clinicforce_qr' ? 'ClinicForce shared records' : intake.source === 'patient_upload' ? 'Patient-submitted insurance' : intake.source === 'manual_entry' ? 'Manual insurance details' : 'Clinic insurance intake'}</strong><small>{new Date(intake.created_at).toLocaleString()} · {stateLabel(intake.status)}</small></span></span><button disabled={deleting || cleanupPending} onClick={() => onOpenIntake(intake.id)}>{intake.id === selected.latest_intake_id ? 'Open insurance review' : 'View submission history'} <ChevronRight size={17} /></button></div>)}</section>{selected.account_status === 'automatic' && <p className="staff-small staff-muted">This clinic record was created from submitted insurance details. {selected.identity_status === 'confirmed' ? 'Patient identity has been confirmed by clinic staff.' : 'Patient identity must be confirmed by staff before insurance review.'} No patient login was created.</p>}</div>
  return <><header className="staff-header"><div><div className="staff-eyebrow">Clinical access · Provider workspace</div><h1 ref={heading} tabIndex={-1}>Clinical patients</h1><p className="staff-muted">Patients shared with your clinic, and clinic records created from insurance submissions.</p></div><div className="staff-inbox-actions"><button onClick={() => onRequestCard()}><Mail size={18} /> Request insurance by email</button><button onClick={onPullEcw}>Pull patient from eCW</button><button className="staff-primary" onClick={onAddCard}><Plus size={18} /> Scan or upload card</button></div></header>{error && <p role="alert" className="staff-alert staff-error">{error}</p>}{notice && <p className="staff-alert staff-success" role="status">{notice}</p>}{!!dashboard?.pending_file_cleanup && <p className="staff-alert" role="status">{dashboard.pending_file_cleanup} deleted {dashboard.pending_file_cleanup === 1 ? 'record has' : 'records have'} files awaiting cleanup. <button disabled={deleting || busy} onClick={() => void retryCleanup()}>{deleting ? 'Cleaning up…' : 'Retry file cleanup'}</button></p>}<nav className="clinical-cohorts" aria-label="Patient groups">{(Object.keys(cohortLabel) as Cohort[]).filter(value => value !== 'other' || !!dashboard?.counts.other).map(value => <button key={value} aria-pressed={cohort === value} onClick={() => { setCohort(value); setPage(1) }}><strong>{dashboard?.counts[value] ?? '—'}</strong><span>{cohortLabel[value]}</span></button>)}</nav><div className="staff-search clinical-search"><label className="staff-field"><span>Find a clinical patient</span><input type="search" placeholder="Name, email, or date of birth" value={query} onChange={event => { setQuery(event.target.value); setPage(1) }} /></label><label className="staff-field"><span>Insurance card status</span><select value={cardStatus} onChange={event => { setCardStatus(event.target.value); setPage(1) }}><option value="">All card statuses</option><option value="received">Card received</option><option value="missing">Card missing</option></select></label><button onClick={() => void refresh()} disabled={busy}><RefreshCw size={17} /> Refresh patients</button></div><section className="staff-panel"><div className="staff-section-heading"><h2>{cohortLabel[cohort]}</h2><span className="staff-small staff-muted" role="status">{busy ? 'Loading patients…' : !dashboard ? 'Not loaded' : `${patients.length} ${patients.length === 1 ? 'patient' : 'patients'}`}</span></div>{!dashboard && !busy ? <p className="staff-muted">Patients could not be loaded. Select Refresh patients to try again.</p> : dashboard && !busy && !patients.length ? <div className="staff-empty"><Users size={30} aria-hidden="true" /><h3>{query || cardStatus || cohort !== 'all' ? 'No matching patients' : 'No clinic patients yet'}</h3><p>{query || cardStatus || cohort !== 'all' ? 'Try a different name or patient group.' : 'Scan a card, request a patient upload, or open a patient’s ClinicForce QR to get started.'}</p></div> : <div className="clinical-patient-list">{visiblePatients.map(patient => <article className="clinical-patient-row" key={patient.id}><button className="clinical-patient-open" onClick={() => { setSelected(patient); setError(''); setNotice(''); setCleanupPending(false) }}><span><strong>{name(patient)}</strong><small>Date of birth: {patient.birth_date || 'Needs confirmation'}</small><small>{accountLabel(patient.account_status)}</small></span><span className={`staff-status ${patient.card_status === 'missing' ? 'staff-status-attention' : ''}`}>{patient.card_status === 'received' ? 'Card received' : 'Card missing'}</span><ChevronRight size={19} aria-hidden="true" /></button><button onClick={() => onRequestCard(patient)}><Mail size={17} /> Request insurance card</button></article>)}</div>}{patients.length > 0 && <nav className="clinical-pagination" aria-label="Patient pages">
    <span className="staff-small staff-muted" role="status">Showing {pageStart + 1}–{Math.min(pageStart + PAGE_SIZE, patients.length)} of {patients.length} patients</span>
    <div className="clinical-page-buttons">
      <button aria-label="Previous patient page" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}><ChevronLeft size={18} aria-hidden="true" /></button>
      {Array.from({ length: pageCount }, (_, index) => index + 1).map(number => <button key={number} aria-label={`Patient page ${number}`} aria-current={number === currentPage ? 'page' : undefined} onClick={() => setPage(number)}>{number}</button>)}
      <button aria-label="Next patient page" disabled={currentPage === pageCount} onClick={() => setPage(currentPage + 1)}><ChevronRight size={18} aria-hidden="true" /></button>
    </div>
  </nav>}</section><p className="staff-small staff-muted">This dashboard is visible only to clinic staff. Patient accounts appear here after they are shared or connected to your clinic.</p></>
}
