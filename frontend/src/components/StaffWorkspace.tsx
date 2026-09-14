import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowLeft, Camera, Check, ChevronRight, FileText, FolderOpen, Inbox, LoaderCircle, LogOut, Plus, QrCode, RefreshCw, Upload, Users } from 'lucide-react'
import InsuranceCardScanner from './InsuranceCardScanner'
import { ApiError } from '../services/api/client'
import {
  addStaffMember, confirmStaffPatient, createStaffIntake, downloadStaffDocument, findStaffPatients,
  getStaffIntake, getStaffIntakes, getStaffLibrary, getStaffSession, loginStaff, logoutStaff,
  downloadStaffLibraryDocument, downloadStaffWorkflowDocument, uploadStaffLibrary,
  type StaffIntake, type StaffLibraryDocument, type StaffPatient, type StaffPatientFields, type StaffSession,
} from '../services/api/staff'
import './StaffWorkspace.css'

interface StaffWorkspaceProps {
  onBack: () => void
  onOpenInsurance: (intakeId: string, version: 'v1' | 'v2') => void
  onScanQr: () => void
  initialIntakeId?: string
}

const emptyFields: StaffPatientFields = { given_name: '', family_name: '', birth_date: '', member_id: '', payer: '', group_number: '', relationship: 'self', subscriber_name: '', subscriber_birth_date: '' }
const patientName = (intake: StaffIntake) => [intake.patient?.given_name || intake.fields.given_name, intake.patient?.family_name || intake.fields.family_name].filter(Boolean).join(' ') || 'Patient details needed'
const statusLabel = (status: string) => ({ needs_confirmation: 'Confirm patient', ready: 'Ready to review', in_progress: 'Review in progress', reviewed: 'Staff review complete' } as Record<string, string>)[status] || status.replaceAll('_', ' ')
const dateLabel = (value: string) => new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` }
function patientFieldsFrom(record: StaffIntake): StaffPatientFields {
  const fields = Object.fromEntries(Object.entries(emptyFields).map(([key, fallback]) => {
    const value = record.fields[key as keyof StaffPatientFields]
    return [key, typeof value === 'string' ? value : fallback]
  })) as StaffPatientFields
  fields.subscriber_name ||= record.fields.member_name || ''
  fields.relationship = ({ '18': 'self', '01': 'spouse', '19': 'child', G8: 'other' } as Record<string, string>)[fields.relationship] || fields.relationship || 'self'
  return fields
}

export default function StaffWorkspace({ onBack, onOpenInsurance, onScanQr, initialIntakeId }: StaffWorkspaceProps) {
  const [session, setSession] = useState<StaffSession | null>(null)
  const [restoring, setRestoring] = useState(true)
  const [screen, setScreen] = useState<'inbox' | 'add' | 'review' | 'match' | 'record' | 'members' | 'library'>('inbox')
  const [tab, setTab] = useState<'overview' | 'documents' | 'activity'>('overview')
  const [intakes, setIntakes] = useState<StaffIntake[]>([])
  const [intake, setIntake] = useState<StaffIntake | null>(null)
  const [fields, setFields] = useState<StaffPatientFields>(emptyFields)
  const [candidates, setCandidates] = useState<StaffPatient[]>([])
  const [match, setMatch] = useState('')
  const [identityConfirmed, setIdentityConfirmed] = useState(false)
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [camera, setCamera] = useState(false)
  const [library, setLibrary] = useState<StaffLibraryDocument[] | null>(null)
  const [busy, setBusy] = useState('')
  const [listLoading, setListLoading] = useState(false)
  const [inboxLoaded, setInboxLoaded] = useState(false)
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [appliedSearch, setAppliedSearch] = useState({ query: '', status: '' })
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const heading = useRef<HTMLHeadingElement>(null)
  const errorBox = useRef<HTMLDivElement>(null)
  const uploadInFlight = useRef(false)
  const staffId = session?.id

  useEffect(() => {
    let active = true
    getStaffSession().then(({ staff }) => { if (active) setSession(staff) })
      .catch(caught => { if (active && !(caught instanceof ApiError && caught.status === 401)) setError(caught instanceof Error ? caught.message : 'Unable to restore your staff session.') })
      .finally(() => { if (active) setRestoring(false) })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (!session) return
    let active = true
    setListLoading(true)
    getStaffIntakes().then(result => { if (active) { setIntakes(result.intakes); setInboxLoaded(true); setNextOffset(result.has_more ? result.next_offset : null); setAppliedSearch({ query: '', status: '' }) } })
      .catch(caught => { if (active) setError(caught instanceof Error ? caught.message : 'Unable to load the insurance inbox.') })
      .finally(() => { if (active) setListLoading(false) })
    return () => { active = false }
  }, [session])

  useEffect(() => {
    if (!staffId || !initialIntakeId) return
    let active = true
    setBusy('open')
    getStaffIntake(initialIntakeId).then(record => {
      if (!active) return
      setIntake(record); setTab('overview')
      if (!record.patient) {
        setFields(patientFieldsFrom(record))
        setIdentityConfirmed(false); setMatch(''); setScreen('review')
      } else setScreen('record')
    }).catch(caught => { if (active) setError(caught instanceof Error ? caught.message : 'Unable to open this intake.') })
      .finally(() => { if (active) setBusy('') })
    return () => { active = false }
  }, [staffId, initialIntakeId])

  useEffect(() => { heading.current?.focus() }, [screen, session, restoring, camera])
  useEffect(() => { if (error) errorBox.current?.focus() }, [error])

  async function perform(name: string, action: () => Promise<void>) {
    setBusy(name); setError(''); setNotice('')
    try { await action() } catch (caught) {
      if (session && caught instanceof ApiError && caught.status === 401) {
        setSession(null); setIntakes([]); setIntake(null); setFiles([]); setFields(emptyFields); setCandidates([]); setCamera(false); setLibrary(null); setInboxLoaded(false); setNextOffset(null)
        setError('Your staff session has ended. Sign in again to continue.')
      } else setError(caught instanceof Error ? caught.message : 'This action could not be completed. Please try again.')
    } finally { setBusy('') }
  }

  function navigate(next: typeof screen) { setScreen(next); setError(''); setNotice(''); setCamera(false) }
  function editPatient(record: StaffIntake) {
    setFields(patientFieldsFrom(record))
    setIdentityConfirmed(false); setMatch(''); navigate('review')
  }
  async function refreshInbox(append = false) {
    const search = append ? appliedSearch : { query, status }
    const result = await getStaffIntakes(search.query, search.status, append ? nextOffset || 0 : 0)
    setIntakes(current => append ? [...current, ...result.intakes.filter(record => !current.some(existing => existing.id === record.id))] : result.intakes)
    setAppliedSearch(search); setNextOffset(result.has_more ? result.next_offset : null); setInboxLoaded(true)
  }
  function openLibrary() { navigate('library'); void perform('library', async () => { const result = await getStaffLibrary(); setLibrary(result.documents) }) }
  function openIntake(id: string) {
    void perform('open', async () => { const record = await getStaffIntake(id); setIntake(record); setTab('overview'); navigate('record') })
  }
  function checkFiles(next: File[]) {
    if (!next.length || next.length > 2) return 'Choose one or two files for the front and back of the card.'
    if (next.some(file => !['image/jpeg', 'image/png', 'application/pdf'].includes(file.type))) return 'Use a JPG, PNG, or PDF file.'
    if (next.some(file => file.size > 10 * 1024 * 1024 || file.size === 0)) return 'Each file must contain data and be no larger than 10 MB.'
    return ''
  }
  function saveCard(next: File[]) {
    if (uploadInFlight.current || busy) return
    const problem = checkFiles(next)
    if (problem) { setError(problem); return }
    uploadInFlight.current = true
    setFiles(next); setCamera(false)
    void perform('upload', async () => {
      const record = await createStaffIntake(next)
      setIntake(record); setIntakes(current => [record, ...current.filter(row => row.id !== record.id)])
      setFiles([]); editPatient(record)
      setNotice('Insurance card saved. Confirm the patient details before linking a clinic record.')
    }).finally(() => { uploadInFlight.current = false })
  }
  function findMatches(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void perform('matching', async () => {
      const result = await findStaffPatients({ given_name: fields.given_name.trim(), family_name: fields.family_name.trim(), birth_date: fields.birth_date })
      setCandidates(result.patients); setMatch(result.patients.length ? '' : 'new'); setIdentityConfirmed(false); navigate('match')
    })
  }
  function savePatient(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!intake || !identityConfirmed || !match) return
    void perform('saving', async () => {
      const record = await confirmStaffPatient(intake.id, { ...fields, ...(match === 'new' ? {} : { patient_id: match }), identity_confirmed: true })
      setIntake(record); setIntakes(current => current.map(row => row.id === record.id ? record : row)); setTab('overview'); navigate('record')
      setNotice('Patient confirmed. Insurance is saved to this clinic without creating a patient account.')
    })
  }
  function input(label: string, key: keyof StaffPatientFields, options: { required?: boolean; type?: string; help?: string } = {}) {
    return <label className="staff-field" key={key} htmlFor={`staff-${key}`}><span>{label}{options.required ? ' *' : ''}</span><input id={`staff-${key}`} value={fields[key]} onChange={event => setFields(current => ({ ...current, [key]: event.target.value }))} type={options.type || 'text'} required={options.required} max={options.type === 'date' ? today() : undefined} min={options.type === 'date' ? '1900-01-01' : undefined} aria-describedby={options.help ? `staff-${key}-help` : undefined} disabled={Boolean(busy)} />{options.help && <small id={`staff-${key}-help`}>{options.help}</small>}</label>
  }
  const alerts = <>{error && <div className="staff-alert staff-error" role="alert" tabIndex={-1} ref={errorBox}>{error}</div>}{notice && <div className="staff-alert staff-success" role="status">{notice}</div>}{busy && <div className="staff-progress" role="status"><LoaderCircle className="staff-spin" size={18} aria-hidden="true" />{busy === 'upload' ? 'Saving card and reading its details. Keep this page open.' : busy === 'matching' ? 'Checking clinic patient records…' : 'Working…'}</div>}</>

  if (restoring) return <section className="staff-workspace"><div className="staff-login"><p role="status">Restoring your staff session…</p></div></section>
  if (!session) return <section className="staff-workspace"><div className="staff-login"><button type="button" className="staff-text-button" onClick={onBack}><ArrowLeft size={18} /> Back</button><div className="staff-eyebrow">ClinicForce · Staff workspace</div><h1 tabIndex={-1} ref={heading}>Sign in to your clinic</h1><p className="staff-muted">Review submitted insurance cards and manage patient insurance. Patients can be added without a ClinicForce account.</p>{alerts}<form onSubmit={event => { event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); void perform('login', async () => { const result = await loginStaff(String(data.get('username')).trim(), String(data.get('password'))); form.reset(); setSession(result.staff); setScreen('inbox') }) }}><label className="staff-field"><span>Staff username</span><input name="username" autoComplete="username" required disabled={Boolean(busy)} /></label><label className="staff-field"><span>Password</span><input name="password" type="password" autoComplete="current-password" required disabled={Boolean(busy)} /></label><button className="staff-primary staff-full" disabled={Boolean(busy)}>Sign in</button></form><p className="staff-small staff-muted">Use the staff account provided by your clinic administrator.</p></div></section>

  if (screen === 'add' && camera) return <section className="staff-workspace staff-scan-focus"><div className="staff-scan-content"><h1 className="staff-sr-only" tabIndex={-1} ref={heading}>Scan insurance card</h1>{alerts}<InsuranceCardScanner onCaptured={saveCard} onCancel={() => setCamera(false)} /></div></section>

  return <section className="staff-workspace">
    <aside className="staff-sidebar"><div><div className="staff-brand">clinic<span>force</span></div><p className="staff-small staff-muted">{session.practice_name}</p></div><nav aria-label="Staff navigation"><button className={screen === 'inbox' ? 'staff-current' : ''} onClick={() => { navigate('inbox'); void perform('refresh', refreshInbox) }} disabled={Boolean(busy)}><Inbox size={18} /> Insurance inbox</button><button className={screen === 'library' ? 'staff-current' : ''} onClick={openLibrary} disabled={Boolean(busy)}><FolderOpen size={18} /> Clinic library</button>{session.role === 'admin' && <button className={screen === 'members' ? 'staff-current' : ''} onClick={() => navigate('members')} disabled={Boolean(busy)}><Users size={18} /> Staff access</button>}<button onClick={onBack} disabled={Boolean(busy)}><ArrowLeft size={18} /> ClinicForce home</button></nav><div className="staff-user"><strong>{session.display_name}</strong><span>{session.role === 'admin' ? 'Clinic administrator' : 'Clinic staff'}</span><button className="staff-text-button" disabled={Boolean(busy)} onClick={() => void perform('logout', async () => { await logoutStaff(); setSession(null); setIntakes([]); setIntake(null); setFields(emptyFields); setFiles([]); setCandidates([]); setQuery(''); setStatus(''); setLibrary(null); setInboxLoaded(false); setNextOffset(null) })}><LogOut size={17} /> Sign out</button></div></aside>
    <div className="staff-main" role="region" aria-label="Staff insurance workspace">{alerts}
      {screen === 'inbox' && <>
        <header className="staff-header"><div><div className="staff-eyebrow">Staff workspace</div><h1 tabIndex={-1} ref={heading}>Insurance inbox</h1><p className="staff-muted">Card scans and PDFs submitted to {session.practice_name}.</p></div><button className="staff-primary" disabled={Boolean(busy)} onClick={() => { setFiles([]); navigate('add') }}><Plus size={18} /> Add patient insurance</button></header>
        <form className="staff-search" onSubmit={event => { event.preventDefault(); void perform('search', refreshInbox) }}><label className="staff-field"><span>Find a patient</span><input type="search" placeholder="Name or member ID" value={query} onChange={event => setQuery(event.target.value)} /></label><label className="staff-field"><span>Status</span><select value={status} onChange={event => setStatus(event.target.value)}><option value="">All submissions</option><option value="needs_confirmation">Confirm patient</option><option value="ready">Ready to review</option><option value="in_progress">Review in progress</option><option value="reviewed">Staff review complete</option></select></label><button disabled={Boolean(busy)}>Search</button></form>
        <section className="staff-panel"><div className="staff-section-heading"><h2>Received insurance</h2><button className="staff-text-button" aria-label="Refresh insurance inbox" disabled={Boolean(busy) || listLoading} onClick={() => void perform('refresh', refreshInbox)}><RefreshCw size={17} /> Refresh</button></div>{listLoading ? <p role="status">Loading submissions…</p> : !inboxLoaded ? <p className="staff-muted">Submissions could not be loaded. Select Refresh to try again.</p> : !intakes.length ? <div className="staff-empty"><Inbox size={30} aria-hidden="true" /><h3>{query || status ? 'No matching submissions' : 'Your inbox is ready'}</h3><p>{query || status ? 'Try another name or choose all submissions.' : 'Scan an insurance card or upload a card PDF to get started.'}</p></div> : <div className="staff-intake-list">{intakes.map(record => <button className="staff-intake-row" key={record.id} disabled={Boolean(busy)} onClick={() => openIntake(record.id)}><span><strong>{patientName(record)}</strong><small>{record.patient?.birth_date || record.fields.birth_date || 'Date of birth needed'}</small></span><span>{record.fields.payer || 'Insurance company needs review'}<small>{record.source === 'clinicforce_qr' ? 'ClinicForce QR' : `${record.documents.length} card ${record.documents.length === 1 ? 'file' : 'files'}`} · {dateLabel(record.created_at)}</small></span><span className={`staff-status ${record.status === 'needs_confirmation' ? 'staff-status-attention' : ''}`}>{statusLabel(record.status)}</span><ChevronRight size={19} aria-hidden="true" /></button>)}</div>}{inboxLoaded && intakes.length > 0 && <div className="staff-pagination"><span className="staff-small staff-muted" role="status">Showing {intakes.length} {intakes.length === 1 ? 'submission' : 'submissions'}</span>{nextOffset !== null && <button disabled={Boolean(busy)} onClick={() => void perform('load-more', () => refreshInbox(true))}>Load more submissions</button>}</div>}</section>
      </>}
      {screen === 'add' && <div className="staff-narrow"><header className="staff-header"><div><div className="staff-eyebrow">Add insurance</div><h1 tabIndex={-1} ref={heading}>Start with the insurance card</h1><p className="staff-muted">No patient account is required. Confirm the patient after the card is saved.</p></div></header><div className="staff-two-columns"><section className="staff-panel"><Camera className="staff-feature-icon" size={30} /><h2>Scan the card</h2><p className="staff-muted">Use the camera to capture the front and back.</p><button className="staff-primary" disabled={Boolean(busy)} onClick={() => setCamera(true)}>Open card scanner</button></section><form className="staff-panel" onSubmit={event => { event.preventDefault(); saveCard(files) }}><Upload className="staff-feature-icon" size={30} /><h2>Upload a photo or PDF</h2><label className="staff-field"><span>Card files</span><input type="file" accept="image/jpeg,image/png,application/pdf" multiple onChange={event => { const next = Array.from(event.target.files || []); setFiles(next); setError(next.length ? checkFiles(next) : '') }} disabled={Boolean(busy)} aria-describedby="staff-upload-help" /><small id="staff-upload-help">JPG, PNG, or PDF · Up to 2 files and 2 pages total · 10 MB per file.</small></label><button className="staff-primary" disabled={Boolean(busy) || !files.length}>Save card and continue</button></form></div><section className="staff-panel staff-qr-option"><div><h2>Patient has a ClinicForce QR?</h2><p className="staff-muted">Open the insurance and records they choose to share with their passcode.</p></div><button onClick={onScanQr} disabled={Boolean(busy)}><QrCode size={18} /> Scan ClinicForce QR</button></section><button className="staff-text-button" onClick={() => navigate('inbox')} disabled={Boolean(busy)}><ArrowLeft size={18} /> Back to inbox</button></div>}
      {screen === 'review' && intake && <div className="staff-narrow"><header className="staff-header"><div><div className="staff-eyebrow">Confirm patient · 1 of 2</div><h1 tabIndex={-1} ref={heading}>Check the patient details</h1><p className="staff-muted">The name on the insurance card may belong to the policyholder.</p></div></header>{intake.warning && <div className="staff-alert staff-warning">{intake.warning} The uploaded card is saved. Enter or correct the details below.</div>}<form className="staff-panel" onSubmit={findMatches}><h2>Patient receiving care</h2><p className="staff-small staff-muted">Fields marked * are required. Confirm these details with the patient.</p><div className="staff-two-columns">{input('First name', 'given_name', { required: true })}{input('Last name', 'family_name', { required: true })}{input('Date of birth', 'birth_date', { required: true, type: 'date' })}<label className="staff-field"><span>Relationship to policyholder *</span><select value={fields.relationship} onChange={event => setFields(current => ({ ...current, relationship: event.target.value }))} disabled={Boolean(busy)}><option value="self">Self</option><option value="spouse">Spouse</option><option value="child">Child</option><option value="other">Other</option></select></label></div><h2>Insurance details</h2><p className="staff-small staff-muted">Review extracted card text before saving. A card does not confirm current coverage.</p><div className="staff-two-columns">{input('Insurance company', 'payer', { required: intake.source !== 'clinicforce_qr' })}{input('Member ID', 'member_id', { required: intake.source !== 'clinicforce_qr' })}{input('Group number', 'group_number')}{input('Policyholder name', 'subscriber_name', { required: fields.relationship !== 'self' })}{fields.relationship !== 'self' && input('Policyholder date of birth', 'subscriber_birth_date', { type: 'date', help: 'Add if known. Some insurers need this for dependent coverage.' })}</div><div className="staff-actions"><button type="button" className="staff-text-button" disabled={Boolean(busy)} onClick={() => navigate('record')}><ArrowLeft size={18} /> View saved card</button><button className="staff-primary" disabled={Boolean(busy)}>Find clinic record <ChevronRight size={18} /></button></div></form><section className="staff-panel"><h2>Saved card files</h2>{!intake.documents.length && <p className="staff-muted">This intake started from a ClinicForce QR. Insurance files can be added in the document workflow after confirming the patient.</p>}{intake.documents.map(doc => <div className="staff-document" key={doc.id}><span><FileText size={17} /> {doc.filename}</span><button type="button" disabled={Boolean(busy)} onClick={() => void perform('download', () => downloadStaffDocument(intake.id, doc.id, doc.filename))}>Download</button></div>)}</section></div>}
      {screen === 'match' && intake && <div className="staff-narrow"><header className="staff-header"><div><div className="staff-eyebrow">Confirm patient · 2 of 2</div><h1 tabIndex={-1} ref={heading}>{candidates.length ? 'Confirm the clinic record' : 'Create the clinic record'}</h1><p className="staff-muted">{fields.given_name} {fields.family_name} · {fields.birth_date}</p></div></header><form className="staff-panel" onSubmit={savePatient}><h2>{candidates.length ? 'Possible matches' : 'No matching clinic patient found'}</h2><p className="staff-muted">{candidates.length ? 'Compare the details with the patient. Similar records are not linked automatically.' : 'Save this patient to your clinic. This does not create a patient login or an eClinicalWorks record.'}</p>{candidates.map(patient => <label className="staff-match-option" key={patient.id}><input type="radio" name="patient-match" value={patient.id} checked={match === patient.id} onChange={() => setMatch(patient.id)} disabled={Boolean(busy)} required /><span><strong>{patient.given_name} {patient.family_name}</strong><small>Date of birth: {patient.birth_date}</small><small>Clinic record: {patient.id}</small></span></label>)}<label className="staff-match-option"><input type="radio" name="patient-match" value="new" checked={match === 'new'} onChange={() => setMatch('new')} disabled={Boolean(busy)} required /><span><strong>Create a new clinic patient record</strong><small>Keep this intake separate from existing patients.</small></span></label><label className="staff-confirmation"><input type="checkbox" checked={identityConfirmed} onChange={event => setIdentityConfirmed(event.target.checked)} required disabled={Boolean(busy)} /><span>I confirmed the patient's name, date of birth, and selected record.</span></label><div className="staff-actions"><button type="button" className="staff-text-button" onClick={() => navigate('review')} disabled={Boolean(busy)}><ArrowLeft size={18} /> Edit details</button><button className="staff-primary" disabled={Boolean(busy) || !identityConfirmed || !match}><Check size={18} /> Save patient and continue</button></div></form></div>}
      {screen === 'record' && intake && <>
        <button className="staff-text-button" onClick={() => { navigate('inbox'); void perform('refresh', refreshInbox) }} disabled={Boolean(busy)}><ArrowLeft size={18} /> Insurance inbox</button><header className="staff-header"><div><div className="staff-eyebrow">Patient insurance workspace</div><h1 tabIndex={-1} ref={heading}>{patientName(intake)}</h1><p className="staff-muted">{intake.fields.payer || 'Insurance company needs review'} · Received {dateLabel(intake.created_at)}</p></div><span className="staff-status">{statusLabel(intake.status)}</span></header><nav className="staff-tabs" aria-label="Patient insurance sections">{(['overview', 'documents', 'activity'] as const).map(value => <button key={value} onClick={() => setTab(value)} aria-current={tab === value ? 'page' : undefined}>{value === 'overview' ? 'Overview' : value === 'documents' ? 'Documents' : 'Activity'}</button>)}</nav>
        {tab === 'overview' && <><div className="staff-two-columns"><section className="staff-panel"><h2>{intake.patient ? 'Continue insurance review' : 'Confirm this patient first'}</h2><p className="staff-muted">{intake.patient ? 'Choose the information available for this visit. Both paths stay attached to this patient intake.' : 'Confirm the name and date of birth before linking patient details or opening the insurance workflow.'}</p>{intake.patient ? <div className="staff-workflow-actions"><button className="staff-primary" onClick={() => onOpenInsurance(intake.id, 'v1')}>Review uploaded documents <ChevronRight size={18} /></button><button onClick={() => onOpenInsurance(intake.id, 'v2')}>Check connected insurance <ChevronRight size={18} /></button></div> : <button className="staff-primary" onClick={() => editPatient(intake)}>Confirm patient details</button>}<p className="staff-small staff-muted">Connected results depend on the clinic's configured eClinicalWorks and Availity access. Missing results remain available for follow-up.</p></section><section className="staff-panel"><h2>Patient and policy</h2><dl className="staff-facts"><div><dt>Date of birth</dt><dd>{intake.patient?.birth_date || intake.fields.birth_date || 'Not confirmed'}</dd></div><div><dt>Member ID</dt><dd>{intake.fields.member_id || 'Not confirmed'}</dd></div><div><dt>Policyholder</dt><dd>{intake.fields.subscriber_name || intake.fields.member_name || 'Not confirmed'}</dd></div><div><dt>Relationship</dt><dd>{intake.fields.relationship || 'Not confirmed'}</dd></div></dl><p className="staff-small staff-muted">Saved to {session.practice_name}. A patient account is optional.</p></section></div><section className="staff-panel"><h2>Insurance information sources</h2><div className="staff-document"><span><FileText size={18} /> {intake.source === 'clinicforce_qr' ? 'ClinicForce QR intake' : 'Original card files'}</span><span className="staff-small">{intake.documents.length} card files saved · {dateLabel(intake.created_at)}</span></div><p className="staff-small staff-muted">Uploaded benefits PDFs, clinic fee schedules, and insurance contracts are reviewed in the document workflow. Coverage, authorization, and estimates retain their source information.</p>{intake.warning && <div className="staff-alert staff-warning">{intake.warning}</div>}</section></>}
        {tab === 'documents' && <><section className="staff-panel"><h2>Original insurance card</h2><p className="staff-muted">{intake.documents.length ? 'Files captured or uploaded by clinic staff.' : 'This intake started from a ClinicForce QR. No separate card file has been uploaded.'}</p>{intake.documents.map(doc => <div className="staff-document" key={doc.id}><span><FileText size={18} /><span><strong>{doc.filename}</strong><small>{dateLabel(doc.created_at)} · {Math.max(1, Math.round(doc.size_bytes / 1024))} KB</small></span></span><button disabled={Boolean(busy)} onClick={() => void perform('download', () => downloadStaffDocument(intake.id, doc.id, doc.filename))}>Download</button></div>)}{intake.patient && <div className="staff-actions"><button onClick={() => onOpenInsurance(intake.id, 'v1')}>Open document review <ChevronRight size={18} /></button></div>}</section><section className="staff-panel"><h2>Supporting insurance documents</h2><p className="staff-muted">Benefits, contracts, fees, and supporting files saved during this patient’s insurance review.</p>{intake.workflow_documents?.length ? intake.workflow_documents.map(doc => <div className="staff-document" key={doc.id}><span><FileText size={18} /><span><strong>{doc.filename}</strong><small>{doc.kind.replaceAll('_', ' ')} · {doc.version === 'v1' ? 'Document review' : 'Connected insurance'}</small><small>{doc.observed_at ? `Source recorded ${dateLabel(doc.observed_at)}` : 'Source date unavailable'}</small></span></span><button disabled={Boolean(busy)} onClick={() => void perform('download', () => downloadStaffWorkflowDocument(intake.id, doc))}>Download</button></div>) : <p className="staff-small staff-muted">No supporting documents have been saved in this intake’s insurance workflows yet.</p>}</section></>}
        {tab === 'activity' && <section className="staff-panel"><h2>Insurance activity</h2>{intake.events?.length ? <ol className="staff-timeline">{intake.events.map(event => <li key={event.id}><strong>{event.action.replaceAll('_', ' ')}</strong><small>{dateLabel(event.created_at)}{event.actor ? ` · ${event.actor}` : ''}</small></li>)}</ol> : <p className="staff-muted">No additional activity has been recorded.</p>}</section>}
      </>}
      {screen === 'library' && <><header className="staff-header"><div><div className="staff-eyebrow">Practice references</div><h1 tabIndex={-1} ref={heading}>Clinic library</h1><p className="staff-muted">Fee schedules and payer contracts shared across this clinic’s insurance workflows.</p></div><button onClick={openLibrary} disabled={Boolean(busy)}><RefreshCw size={18} /> Refresh</button></header><section className="staff-panel"><h2>Saved clinic documents</h2><p className="staff-small staff-muted">Staff select the applicable document and version during insurance review. A matching payer name does not apply a contract automatically.</p>{library === null ? <p className="staff-muted">{busy === 'library' ? 'Loading clinic documents…' : 'Clinic documents have not been loaded. Select Refresh to try again.'}</p> : !library.length ? <div className="staff-empty"><FolderOpen size={30} aria-hidden="true" /><h3>No clinic documents yet</h3><p>{session.role === 'admin' ? 'Add a fee schedule or contract below.' : 'Your clinic administrator can add fee schedules and contracts.'}</p></div> : library.map(doc => <div className="staff-document" key={doc.id}><span><FileText size={18} /><span><strong>{doc.label}</strong><small>{doc.kind === 'contract' ? 'Payer contract' : doc.kind === 'self_pay' ? 'Self-pay rates' : 'Fee schedule'}{doc.payer ? ` · ${doc.payer}` : ''}</small><small>{doc.filename} · Added {dateLabel(doc.created_at)}</small><small>Effective {doc.effective_from || 'date not specified'}{doc.effective_to ? ` to ${doc.effective_to}` : ''}</small></span></span><button disabled={Boolean(busy)} onClick={() => void perform('download', () => downloadStaffLibraryDocument(doc.id, doc.filename))}>Download</button></div>)}</section>{session.role === 'admin' && <form className="staff-panel" onSubmit={event => { event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); const from = String(data.get('effective_from') || ''); const to = String(data.get('effective_to') || ''); if (from && to && to < from) { setError('The effective end date must be on or after the start date.'); return } void perform('library-upload', async () => { const document = await uploadStaffLibrary(data); setLibrary(current => [document, ...(current || [])]); form.reset(); setNotice('Clinic document saved. Staff can select this version during insurance review.') }) }}><h2>Add a clinic document</h2><div className="staff-two-columns"><label className="staff-field"><span>Document type</span><select name="kind" disabled={Boolean(busy)}><option value="fee_schedule">Fee schedule</option><option value="contract">Payer contract</option><option value="self_pay">Self-pay rates</option></select></label><label className="staff-field"><span>Label *</span><input name="label" required placeholder="2026 office fee schedule" disabled={Boolean(busy)} /></label><label className="staff-field"><span>Payer, if applicable</span><input name="payer" disabled={Boolean(busy)} /></label><label className="staff-field"><span>File *</span><input type="file" name="file" accept=".csv,.pdf,application/pdf,text/csv" required disabled={Boolean(busy)} /><small>PDF for contracts; CSV for fee schedules and self-pay rates.</small></label><label className="staff-field"><span>Effective from</span><input name="effective_from" type="date" disabled={Boolean(busy)} /></label><label className="staff-field"><span>Effective to</span><input name="effective_to" type="date" disabled={Boolean(busy)} /></label></div><button className="staff-primary" disabled={Boolean(busy)}><Upload size={18} /> Save clinic document</button></form>}</>}
      {screen === 'members' && session.role === 'admin' && <div className="staff-narrow"><header className="staff-header"><div><div className="staff-eyebrow">Clinic administration</div><h1 tabIndex={-1} ref={heading}>Add a staff account</h1><p className="staff-muted">Grant access to the insurance inbox for {session.practice_name}.</p></div></header><form className="staff-panel" onSubmit={event => { event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); void perform('member', async () => { await addStaffMember({ username: String(data.get('username')).trim(), display_name: String(data.get('display_name')).trim(), password: String(data.get('password')), role: data.get('role') === 'admin' ? 'admin' : 'staff' }); form.reset(); setNotice('Staff account created. Share the credentials through your clinic’s approved channel.') }) }}><label className="staff-field"><span>Display name</span><input name="display_name" required disabled={Boolean(busy)} /></label><label className="staff-field"><span>Username</span><input name="username" autoComplete="off" required disabled={Boolean(busy)} /></label><label className="staff-field"><span>Password</span><input name="password" type="password" autoComplete="new-password" minLength={12} required disabled={Boolean(busy)} /><small>Use at least 12 characters.</small></label><label className="staff-field"><span>Access level</span><select name="role" disabled={Boolean(busy)}><option value="staff">Staff · Patient insurance</option><option value="admin">Administrator · Patient insurance and staff accounts</option></select></label><button className="staff-primary" disabled={Boolean(busy)}><Plus size={18} /> Create staff account</button></form></div>}
    </div>
  </section>
}
