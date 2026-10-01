import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowLeft, Camera, Check, ChevronRight, ExternalLink, FileText, FolderOpen, Inbox, LoaderCircle, LogOut, Plus, QrCode, RefreshCw, Upload, Users } from 'lucide-react'
import InsuranceCards from './InsuranceCards'
import './CommercialEstimator.css'
import InsuranceCardScanner from './InsuranceCardScanner'
import StaffUploadLinks from './StaffUploadLinks'
import StaffShareLink from './StaffShareLink'
import ClinicalPatientDashboard from './ClinicalPatientDashboard'
import { ApiError } from '../services/api/client'
import {
  createStaffInvitation, getStaffInvitations, revokeStaffInvitation, readStaffInsuranceCard, type StaffInvitation, type SavedStaffInvitation, type ClinicalPatient,
  addStaffMember, confirmStaffPatient, createStaffEcwLookup, createStaffIntake, downloadStaffDocument, findStaffPatients,
  getStaffIntake, getStaffIntakes, getStaffLibrary, getStaffSession, loginStaff, logoutStaff,
  downloadStaffLibraryDocument, downloadStaffWorkflowDocument, getStaffEra835, uploadStaffLibrary,
  type EcwPatientMatch, type Era835Summary, type StaffCardDetails, type StaffIntake, type StaffLibraryDocument, type StaffPatient, type StaffPatientFields, type StaffSession,
} from '../services/api/staff'
import './StaffWorkspace.css'

interface StaffWorkspaceProps {
  onBack: () => void
  onOpenInsurance: (intakeId: string, version: 'v1' | 'v2' | 'commercial', refreshBenefits?: boolean) => void
  onScanQr: () => void
  onCreateClinic: () => void
  onJoinClinic: () => void
  onOpenPatientQr: () => void
  initialIntakeId?: string
}

const emptyFields: StaffPatientFields = { given_name: '', family_name: '', birth_date: '', member_id: '', payer: '', group_number: '', relationship: 'unknown', subscriber_name: '', subscriber_birth_date: '' }
const emptyCardDetails: StaffCardDetails = { full_name: '', member_id: '', birth_date: '' }
const patientName = (intake: StaffIntake) => [intake.patient?.given_name || intake.fields.given_name, intake.patient?.family_name || intake.fields.family_name].filter(Boolean).join(' ') || 'Patient details needed'
const statusLabel = (status: string) => ({ needs_confirmation: 'Confirm patient', ready: 'Ready to review', in_progress: 'Review in progress', reviewed: 'Staff review complete' } as Record<string, string>)[status] || status.replaceAll('_', ' ')
const dateLabel = (value: string) => new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
const money = (cents: number) => new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(cents / 100)
const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}` }
function patientFieldsFrom(record: StaffIntake): StaffPatientFields {
  const fields = Object.fromEntries(Object.entries(emptyFields).map(([key, fallback]) => {
    const value = record.fields[key as keyof StaffPatientFields]
    return [key, typeof value === 'string' ? value : fallback]
  })) as StaffPatientFields
  fields.subscriber_name ||= record.fields.member_name || ''
  if (!fields.given_name && !fields.family_name && record.fields.member_name) {
    const printed = record.fields.member_name.trim()
    const parts = printed.includes(',') ? printed.split(',').reverse().join(' ').trim().split(/\s+/) : printed.split(/\s+/)
    if (parts.length > 1) { fields.given_name = parts.slice(0, -1).join(' '); fields.family_name = parts.at(-1)! }
  }
  fields.relationship = ({ '18': 'self', '01': 'spouse', '19': 'child', G8: 'other' } as Record<string, string>)[fields.relationship] || fields.relationship || 'unknown'
  return fields
}

export default function StaffWorkspace({ onBack, onOpenInsurance, onScanQr, onCreateClinic, onJoinClinic, onOpenPatientQr, initialIntakeId }: StaffWorkspaceProps) {
  const [historyPatientId, setHistoryPatientId] = useState<string>()
  const [linkPatient, setLinkPatient] = useState<{ id?: string; intakeId?: string; name: string; email?: string } | null>(null)
  const [recordBack, setRecordBack] = useState<'patients' | 'inbox'>('patients')
  const [linkBack, setLinkBack] = useState<'patients' | 'inbox' | 'record'>('patients')
  const [invitations, setInvitations] = useState<SavedStaffInvitation[] | null>(null)
  const [invitation, setInvitation] = useState<StaffInvitation | null>(null)
  const [session, setSession] = useState<StaffSession | null>(null)
  const [restoring, setRestoring] = useState(true)
  const [screen, setScreen] = useState<'patients' | 'inbox' | 'lookup' | 'add' | 'review' | 'match' | 'record' | 'members' | 'library' | 'links'>('patients')
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
  const [lookupDetails, setLookupDetails] = useState({ given_name: '', middle_initial: '', family_name: '', birth_date: '' })
  const [ecwMatch, setEcwMatch] = useState<EcwPatientMatch | null>(null)
  const [camera, setCamera] = useState(false)
  const [library, setLibrary] = useState<StaffLibraryDocument[] | null>(null)
  const [era, setEra] = useState<Era835Summary | null>(null)
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
  const usesAutomaticRecord = intake?.auto_patient?.identity_status === 'unverified'

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

  useEffect(() => {
    if (screen !== 'members' || session?.role !== 'admin') return
    let active = true
    getStaffInvitations().then(result => { if (active) setInvitations(result.invitations) }).catch(caught => { if (active) setError(caught instanceof Error ? caught.message : 'Staff invitations could not be loaded.') })
    return () => { active = false }
  }, [screen, session, invitation])

  useEffect(() => { heading.current?.focus() }, [screen, session, restoring, camera])
  useEffect(() => { if (error) errorBox.current?.focus() }, [error])

  async function perform(name: string, action: () => Promise<void>) {
    setBusy(name); setError(''); setNotice('')
    try { await action() } catch (caught) {
      if (session && caught instanceof ApiError && caught.status === 401) {
        setSession(null); setInvitation(null); setInvitations(null); setLinkPatient(null); setIntakes([]); setIntake(null); setFiles([]); setFields(emptyFields); setCandidates([]); setCamera(false); setLibrary(null); setInboxLoaded(false); setNextOffset(null)
        setError('Your staff session has ended. Sign in again to continue.')
      } else if (name === 'upload' && caught instanceof ApiError && [0, 408, 502, 504].includes(caught.status)) {
        setError('Upload confirmation was interrupted. The card may already be saved. Open the insurance inbox and refresh before uploading it again.')
      } else setError(caught instanceof Error ? caught.message : 'This action could not be completed. Please try again.')
    } finally { setBusy('') }
  }

  function navigate(next: typeof screen) { if (next === 'patients') setHistoryPatientId(undefined); setScreen(next); setError(''); setNotice(''); setCamera(false) }
  function editPatient(record: StaffIntake) {
    setFields(patientFieldsFrom(record))
    setIdentityConfirmed(false); setMatch(''); navigate('review')
  }
  function retryEcwLookup(record: StaffIntake) {
    setLookupDetails({ given_name: record.fields.given_name || '', middle_initial: record.fields.middle_initial || '',
      family_name: record.fields.family_name || '', birth_date: record.fields.birth_date || '' })
    navigate('lookup')
  }
  async function refreshInbox(append = false) {
    const search = append ? appliedSearch : { query, status }
    const result = await getStaffIntakes(search.query, search.status, append ? nextOffset || 0 : 0)
    setIntakes(current => append ? [...current, ...result.intakes.filter(record => !current.some(existing => existing.id === record.id))] : result.intakes)
    setAppliedSearch(search); setNextOffset(result.has_more ? result.next_offset : null); setInboxLoaded(true)
  }
  function requestPatientCard(patient?: ClinicalPatient) { setLinkBack('patients'); setLinkPatient(patient ? { ...(patient.clinic_patient_id ? { id: patient.clinic_patient_id } : { intakeId: patient.latest_intake_id || undefined }), name: `${patient.given_name} ${patient.family_name}`, email: patient.email || undefined } : null); navigate('links') }
  function openLibrary() { navigate('library'); void perform('library', async () => { const result = await getStaffLibrary(); setLibrary(result.documents); setEra(null) }) }
  function openIntake(id: string) {
    setRecordBack(screen === 'inbox' ? 'inbox' : 'patients')
    void perform('open', async () => {
      const record = await getStaffIntake(id)
      setEcwMatch(null); setIntake(record); setTab('overview')
      if (screen === 'patients' && record.patient && (record.patient.ecw_patient_id || record.fields.ecw_patient_id)) {
        onOpenInsurance(record.id, 'commercial'); return
      }
      navigate('record')
    })
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
      const record = await createStaffIntake(next, emptyCardDetails)
      setIntake(record); setIntakes(current => [record, ...current.filter(row => row.id !== record.id)])
      setFiles([]); editPatient(record)
      setNotice('Insurance card saved. Confirm the patient details before linking a clinic record.')
    }).finally(() => { uploadInFlight.current = false })
  }
  function startEcwLookup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void perform('lookup', async () => {
      const { ecw_match, ...record } = await createStaffEcwLookup(lookupDetails)
      setIntake(record); setIntakes(current => [record, ...current.filter(row => row.id !== record.id)])
      setEcwMatch(ecw_match); setFields(patientFieldsFrom(record))
      if (record.patient) { onOpenInsurance(record.id, 'commercial', false); return }
      const result = await findStaffPatients({ given_name: record.fields.given_name || '', family_name: record.fields.family_name || '', birth_date: record.fields.birth_date || '' })
      // eCW already matched this exact name and date of birth. When one clinic
      // record (or none) has the same identity, open the estimate directly.
      // Ambiguous records still require an explicit staff choice.
      if (result.patients.length <= 1) {
        try {
          const confirmed = await confirmStaffPatient(record.id, { ...patientFieldsFrom(record), ...(result.patients[0] ? { patient_id: result.patients[0].id } : {}), identity_confirmed: true })
          setIntake(confirmed); setIntakes(current => current.map(row => row.id === confirmed.id ? confirmed : row))
          onOpenInsurance(confirmed.id, 'commercial', false)
          return
        } catch {
          // A concurrent eCW link or changed clinic record is handled by the
          // existing explicit match screen below.
        }
      }
      setCandidates(result.patients); setMatch(result.patients.length ? '' : 'new'); setIdentityConfirmed(false)
      setLookupDetails({ given_name: '', middle_initial: '', family_name: '', birth_date: '' })
      navigate('match')
    })
  }
  function findMatches(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (intake?.source === 'ecw_lookup' && !intake.fields.ecw_patient_id) {
      setError('This intake was saved before eCW verification. Start a new eCW lookup.'); return
    }
    void perform('matching', async () => {
      const result = await findStaffPatients({ given_name: fields.given_name.trim(), family_name: fields.family_name.trim(), birth_date: fields.birth_date })
      const matches = result.patients.filter(patient => patient.id !== intake?.expected_patient_id && !(usesAutomaticRecord && patient.id === intake?.auto_patient_id)); setCandidates(matches); setMatch(matches.length ? '' : 'new'); setIdentityConfirmed(false); navigate('match')
    })
  }
  function savePatient(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!intake || !identityConfirmed || !match) return
    void perform('saving', async () => {
      const record = await confirmStaffPatient(intake.id, { ...fields, ...(match === 'new' ? {} : { patient_id: match }), identity_confirmed: true })
      setIntake(record); setIntakes(current => current.map(row => row.id === record.id ? record : row)); setTab('overview')
      if (record.source === 'ecw_lookup') { onOpenInsurance(record.id, 'commercial', false); return }
      navigate('record')
      setNotice('Patient confirmed. Insurance is saved to this clinic without creating a patient account.')
    })
  }
  function input(label: string, key: keyof StaffPatientFields, options: { required?: boolean; type?: string; help?: string } = {}) {
    return <label className="staff-field" key={key} htmlFor={`staff-${key}`}><span>{label}{options.required ? ' *' : ''}</span><input id={`staff-${key}`} value={fields[key]} onChange={event => setFields(current => ({ ...current, [key]: event.target.value }))} type={options.type || 'text'} required={options.required} max={options.type === 'date' ? today() : undefined} min={options.type === 'date' ? '1900-01-01' : undefined} aria-describedby={options.help ? `staff-${key}-help` : undefined} disabled={Boolean(busy)} />{options.help && <small id={`staff-${key}-help`}>{options.help}</small>}</label>
  }
  const alerts = <>{error && <div className="staff-alert staff-error" role="alert" tabIndex={-1} ref={errorBox}>{error}</div>}{notice && <div className="staff-alert staff-success" role="status">{notice}</div>}{busy && <div className="staff-progress" role="status"><LoaderCircle className="staff-spin" size={18} aria-hidden="true" />{busy === 'upload' ? 'Saving card and reading its details. Keep this page open.' : busy === 'matching' ? 'Checking clinic patient records…' : 'Working…'}</div>}</>

  if (restoring) return <section className="staff-workspace"><div className="staff-login"><p role="status">Restoring your staff session…</p></div></section>
  if (!session) return <section className="staff-workspace"><div className="staff-login"><button type="button" className="staff-text-button" onClick={onBack}><ArrowLeft size={18} /> Back</button><div className="staff-eyebrow">ClinicForce · Staff workspace</div><h1 tabIndex={-1} ref={heading}>Sign in to your clinic</h1><p className="staff-muted">Review submitted insurance cards and manage patient insurance. Patients can be added without a ClinicForce account.</p>{alerts}<form onSubmit={event => { event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); void perform('login', async () => { const result = await loginStaff(String(data.get('username')).trim(), String(data.get('password'))); form.reset(); setSession(result.staff); setScreen('patients') }) }}><label className="staff-field"><span>Email or staff username</span><input name="username" autoComplete="username" required disabled={Boolean(busy)} /></label><label className="staff-field"><span>Password</span><input name="password" type="password" autoComplete="current-password" required disabled={Boolean(busy)} /></label><button className="staff-primary staff-full" disabled={Boolean(busy)}>Sign in</button></form><div className="staff-login-links"><button type="button" onClick={onCreateClinic} disabled={Boolean(busy)}>Create a clinic account <ChevronRight size={17} /></button><button type="button" onClick={onJoinClinic} disabled={Boolean(busy)}>Join your clinic <ChevronRight size={17} /></button><button type="button" className="staff-text-button" onClick={onOpenPatientQr} disabled={Boolean(busy)}><QrCode size={18} /> Open a patient QR</button></div><p className="staff-small staff-muted">Clinic administrators can invite colleagues or create staff accounts. A patient QR opens only the records shared with its passcode.</p></div></section>

  if (busy === 'lookup') return <section className="visit-estimator" aria-labelledby="lookup-estimate-title" aria-busy="true">
    <header className="visit-page-header"><h1 id="lookup-estimate-title">Insurance estimator</h1><span className="visit-practice">{session.practice_name}</span></header>
    <div className="visit-patient-bar"><div><div><strong>{lookupDetails.given_name} {lookupDetails.family_name}</strong><small>DOB {lookupDetails.birth_date}</small></div></div></div>
    <div className="visit-empty" role="status"><LoaderCircle className="visit-spin" size={26} aria-hidden="true" /><h2>Finding the patient and scheduled appointment</h2><p>ClinicForce will pull demographics, insurance and available cards, reconcile eligibility, and prepare the arrival estimate.</p></div>
  </section>

  if (screen === 'add' && camera) return <section className="staff-workspace staff-scan-focus"><div className="staff-scan-content"><h1 className="staff-sr-only" tabIndex={-1} ref={heading}>Scan insurance card</h1>{alerts}<InsuranceCardScanner onCaptured={saveCard} onCancel={() => setCamera(false)} /></div></section>

  return <section className="staff-workspace">
    <aside className="staff-sidebar"><div><div className="staff-brand">clinic<span>force</span></div><p className="staff-small staff-muted">{session.practice_name}</p></div><nav aria-label="Staff navigation"><button className={screen === 'patients' ? 'staff-current' : ''} onClick={() => navigate('patients')} disabled={Boolean(busy)}><Users size={18} /> Clinical patients</button><button className={screen === 'inbox' ? 'staff-current' : ''} onClick={() => { navigate('inbox'); void perform('refresh', refreshInbox) }} disabled={Boolean(busy)}><Inbox size={18} /> Insurance inbox</button><button className={screen === 'links' ? 'staff-current' : ''} onClick={() => { setLinkBack('inbox'); setLinkPatient(null); navigate('links') }} disabled={Boolean(busy)}><Upload size={18} /> Patient upload links</button><button className={screen === 'library' ? 'staff-current' : ''} onClick={openLibrary} disabled={Boolean(busy)}><FolderOpen size={18} /> Clinic library</button>{session.role === 'admin' && <button className={screen === 'members' ? 'staff-current' : ''} onClick={() => navigate('members')} disabled={Boolean(busy)}><Users size={18} /> Staff access</button>}<button onClick={onBack} disabled={Boolean(busy)}><ArrowLeft size={18} /> ClinicForce home</button></nav><div className="staff-user"><strong>{session.display_name}</strong><span>{session.role === 'admin' ? 'Clinic administrator' : 'Clinic staff'}</span><button className="staff-text-button" disabled={Boolean(busy)} onClick={() => void perform('logout', async () => { await logoutStaff(); setSession(null); setInvitation(null); setInvitations(null); setLinkPatient(null); setIntakes([]); setIntake(null); setFields(emptyFields); setFiles([]); setCandidates([]); setQuery(''); setStatus(''); setLibrary(null); setEra(null); setInboxLoaded(false); setNextOffset(null) })}><LogOut size={17} /> Sign out</button></div></aside>
    <div className="staff-main" role="region" aria-label="Staff insurance workspace">{alerts}
      {screen === 'patients' && <ClinicalPatientDashboard initialPatientId={historyPatientId} onPullEcw={() => navigate('lookup')} onOpenIntake={openIntake} onRequestCard={requestPatientCard} onAddCard={() => { setFiles([]); navigate('add') }} />}
      {screen === 'inbox' && <>
        <header className="staff-header"><div><div className="staff-eyebrow">Staff workspace</div><h1 tabIndex={-1} ref={heading}>Insurance inbox</h1><p className="staff-muted">Card scans and PDFs submitted to {session.practice_name}.</p></div><div className="staff-inbox-actions"><button disabled={Boolean(busy)} onClick={() => { setLinkBack('inbox'); setLinkPatient(null); navigate('links') }}>Request patient upload</button><button className="staff-primary" disabled={Boolean(busy)} onClick={() => { setFiles([]); navigate('add') }}><Plus size={18} /> Add patient insurance</button></div></header>
        <form className="staff-search" onSubmit={event => { event.preventDefault(); void perform('search', refreshInbox) }}><label className="staff-field"><span>Find a patient</span><input type="search" placeholder="Name or member ID" value={query} onChange={event => setQuery(event.target.value)} /></label><label className="staff-field"><span>Status</span><select value={status} onChange={event => setStatus(event.target.value)}><option value="">All submissions</option><option value="needs_confirmation">Confirm patient</option><option value="ready">Ready to review</option><option value="in_progress">Review in progress</option><option value="reviewed">Staff review complete</option></select></label><button disabled={Boolean(busy)}>Search</button></form>
        <section className="staff-panel"><div className="staff-section-heading"><h2>Received insurance</h2><button className="staff-text-button" aria-label="Refresh insurance inbox" disabled={Boolean(busy) || listLoading} onClick={() => void perform('refresh', refreshInbox)}><RefreshCw size={17} /> Refresh</button></div>{listLoading ? <p role="status">Loading submissions…</p> : !inboxLoaded ? <p className="staff-muted">Submissions could not be loaded. Select Refresh to try again.</p> : !intakes.length ? <div className="staff-empty"><Inbox size={30} aria-hidden="true" /><h3>{query || status ? 'No matching submissions' : 'Your inbox is ready'}</h3><p>{query || status ? 'Try another name or choose all submissions.' : 'Scan an insurance card or upload a card PDF to get started.'}</p></div> : <div className="staff-intake-list">{intakes.map(record => <button className="staff-intake-row" key={record.id} disabled={Boolean(busy)} onClick={() => openIntake(record.id)}><span><strong>{patientName(record)}</strong><small>{record.patient?.birth_date || record.fields.birth_date || 'Date of birth needed'}</small></span><span>{record.fields.payer || 'Insurance company needs review'}<small>{record.source === 'clinicforce_qr' ? 'ClinicForce QR' : record.source === 'patient_upload' ? (record.patient ? 'Patient upload' : 'Patient upload · Pending staff confirmation') : `${record.documents.length} card ${record.documents.length === 1 ? 'file' : 'files'}`} · {dateLabel(record.created_at)}</small></span><span className={`staff-status ${record.status === 'needs_confirmation' ? 'staff-status-attention' : ''}`}>{statusLabel(record.status)}</span><ChevronRight size={19} aria-hidden="true" /></button>)}</div>}{inboxLoaded && intakes.length > 0 && <div className="staff-pagination"><span className="staff-small staff-muted" role="status">Showing {intakes.length} {intakes.length === 1 ? 'submission' : 'submissions'}</span>{nextOffset !== null && <button disabled={Boolean(busy)} onClick={() => void perform('load-more', () => refreshInbox(true))}>Load more submissions</button>}</div>}</section>
      </>}
      {screen === 'links' && <StaffUploadLinks key={linkPatient?.id || linkPatient?.intakeId || 'new'} patientId={linkPatient?.id} intakeId={linkPatient?.intakeId} patientName={linkPatient?.name} initialEmail={linkPatient?.email} backLabel={linkBack === 'record' ? 'Back to patient' : linkBack === 'inbox' ? 'Back to inbox' : 'Back to clinical patients'} onBack={() => navigate(linkBack)} onOpenIntake={openIntake} />}
      {screen === 'lookup' && <div className="staff-narrow">
        <header className="staff-header"><div><div className="staff-eyebrow">Add insurance</div><h1 tabIndex={-1} ref={heading}>Find patient & estimate</h1><p className="staff-muted">Enter the patient's name and date of birth. ClinicForce will find or update their clinic record, refresh their visit and insurance details, and open the estimate automatically.</p></div></header>
        <section className="staff-panel"><h2>Patient details</h2><form onSubmit={startEcwLookup}><div className="staff-two-columns">
          <label className="staff-field"><span>First name</span><input required maxLength={100} autoComplete="given-name" value={lookupDetails.given_name} onChange={event => setLookupDetails(current => ({ ...current, given_name: event.target.value }))} disabled={Boolean(busy)} /></label>
          <label className="staff-field"><span>Middle initial (optional)</span><input maxLength={10} value={lookupDetails.middle_initial} onChange={event => setLookupDetails(current => ({ ...current, middle_initial: event.target.value }))} disabled={Boolean(busy)} /></label>
          <label className="staff-field"><span>Last name</span><input required maxLength={100} autoComplete="family-name" value={lookupDetails.family_name} onChange={event => setLookupDetails(current => ({ ...current, family_name: event.target.value }))} disabled={Boolean(busy)} /></label>
          <label className="staff-field"><span>Date of birth</span><input required type="date" max={today()} autoComplete="bday" value={lookupDetails.birth_date} onChange={event => setLookupDetails(current => ({ ...current, birth_date: event.target.value }))} disabled={Boolean(busy)} /></label>
        </div><button className="staff-primary" disabled={Boolean(busy)}>Open insurance estimator</button></form></section>
<button onClick={() => navigate('patients')}>Back to patients</button></div>}
      {screen === 'add' && <div className="staff-narrow">
        <section className="staff-panel"><h2>Scan or upload insurance card</h2><p className="staff-muted">We will read the card and fill the available fields. Confirm the patient identity before saving a patient record.</p><form onSubmit={event => { event.preventDefault(); saveCard(files) }}><div className="staff-two-columns"><div><Camera className="staff-feature-icon" size={30} /><h3>Scan the card</h3><button className="staff-primary" type="button" disabled={Boolean(busy)} onClick={() => setCamera(true)}>Open card scanner</button></div><div><Upload className="staff-feature-icon" size={30} /><h3>Upload a photo or PDF</h3><label className="staff-field"><span>Card files</span><input type="file" accept="image/jpeg,image/png,application/pdf" multiple onChange={event => { const next = Array.from(event.target.files || []); setFiles(next); setError(next.length ? checkFiles(next) : '') }} disabled={Boolean(busy)} aria-describedby="staff-upload-help" /><small id="staff-upload-help">JPG, PNG, or PDF · Up to 2 files and 2 pages total · 10 MB per file.</small></label><button disabled={Boolean(busy) || !files.length}>Save card and continue</button></div></div></form></section>
        <section className="staff-panel staff-qr-option"><div><h2>Patient has a ClinicForce QR?</h2><p className="staff-muted">Open the insurance and records they choose to share with their passcode.</p></div><button onClick={onScanQr} disabled={Boolean(busy)}><QrCode size={18} /> Scan ClinicForce QR</button></section>
        <button className="staff-text-button" onClick={() => navigate('inbox')} disabled={Boolean(busy)}><ArrowLeft size={18} /> Back to inbox</button>
      </div>}
      {screen === 'review' && intake && <div className="staff-narrow"><header className="staff-header"><div><div className="staff-eyebrow">Confirm patient · 1 of 2</div><h1 tabIndex={-1} ref={heading}>Check the patient details</h1><p className="staff-muted">{intake.source === 'ecw_lookup' ? 'Confirm the patient name and date of birth before looking up the scheduled visit in eCW.' : 'The name on the insurance card may belong to the policyholder.'}</p></div></header>{intake.source === 'ecw_lookup' && !intake.fields.ecw_patient_id && <div className="staff-alert staff-warning">This intake was saved before eCW verified the patient. <button type="button" onClick={() => retryEcwLookup(intake)}>Search in eCW now</button></div>}{intake.documents.length > 0 && !intake.patient && <section className="staff-panel"><h2>Read the saved insurance card</h2><p className="staff-muted">Confirm the patient’s identity separately from the policyholder printed on the card.{intake.expected_patient_id ? ' This link was requested for an existing patient; verify the record before linking it.' : ''}</p>{intake.expected_patient && <p className="staff-alert staff-warning">Requested for <strong>{intake.expected_patient.given_name} {intake.expected_patient.family_name}</strong> · Date of birth: {intake.expected_patient.birth_date}. Confirm the uploaded details match this patient.</p>}<button disabled={Boolean(busy)} onClick={() => void perform('read-card', async () => { const record = await readStaffInsuranceCard(intake.id); const proposals = patientFieldsFrom(record); setIntake(record); setFields(current => Object.fromEntries(Object.entries(current).map(([key, value]) => [key, value || proposals[key as keyof StaffPatientFields]])) as StaffPatientFields); setNotice('Card reading finished. Review the proposed insurance details before saving.') })}>Read insurance card</button><p className="staff-small staff-muted staff-source-note">Fills missing insurance details from the saved card. You can also enter them manually.</p></section>}{intake.warning && <div className="staff-alert staff-warning">{intake.warning} The uploaded card is saved. Enter or correct the details below.</div>}<form className="staff-panel" onSubmit={findMatches}><h2>Patient receiving care</h2><p className="staff-small staff-muted">Fields marked * are required. Confirm these details with the patient.</p><div className="staff-two-columns">{input('First name', 'given_name', { required: true })}{input('Last name', 'family_name', { required: true })}{input('Date of birth', 'birth_date', { required: true, type: 'date' })}{intake.source !== 'ecw_lookup' && <label className="staff-field"><span>Relationship to policyholder *</span><select value={fields.relationship} onChange={event => setFields(current => ({ ...current, relationship: event.target.value }))} disabled={Boolean(busy)}><option value="unknown">Use eCW coverage</option><option value="self">Self</option><option value="spouse">Spouse</option><option value="child">Child</option><option value="other">Other</option></select></label>}</div>{intake.source !== 'ecw_lookup' && <><h2>Insurance details</h2><p className="staff-small staff-muted">Review extracted card text before saving. A card does not confirm current coverage.</p><div className="staff-two-columns">{input('Insurance company', 'payer', { required: !['clinicforce_qr', 'ecw_lookup'].includes(intake.source || '') })}{input('Member ID', 'member_id', { required: !['clinicforce_qr', 'ecw_lookup'].includes(intake.source || '') })}{input('Group number', 'group_number')}{input('Policyholder name', 'subscriber_name', { required: !['self', 'unknown'].includes(fields.relationship) })}{!['self', 'unknown'].includes(fields.relationship) && input('Policyholder date of birth', 'subscriber_birth_date', { type: 'date', help: 'Add if known. Some insurers need this for dependent coverage.' })}</div></>}<div className="staff-actions"><button type="button" className="staff-text-button" disabled={Boolean(busy)} onClick={() => navigate('record')}><ArrowLeft size={18} /> {intake.source === 'ecw_lookup' ? 'Back to patient' : 'View saved card'}</button><button className="staff-primary" disabled={Boolean(busy)}>Find clinic record <ChevronRight size={18} /></button></div></form>{intake.source !== 'ecw_lookup' && <section className="staff-panel"><h2>Saved card files</h2>{!intake.documents.length && <p className="staff-muted">This intake started from a ClinicForce QR. Insurance files can be added in the document workflow after confirming the patient.</p>}{intake.documents.map(doc => <div className="staff-document" key={doc.id}><span><FileText size={17} /> {doc.filename}</span><button type="button" disabled={Boolean(busy)} onClick={() => void perform('download', () => downloadStaffDocument(intake.id, doc.id, doc.filename))}>Download</button></div>)}</section>}</div>}
      {screen === 'match' && intake && <div className="staff-narrow"><header className="staff-header"><div><div className="staff-eyebrow">Confirm patient · 2 of 2</div><h1 tabIndex={-1} ref={heading}>{intake.source === 'ecw_lookup' ? 'eCW patient found' : candidates.length ? 'Confirm the clinic record' : intake.expected_patient_id || usesAutomaticRecord ? 'Confirm the clinic record' : 'Create the clinic record'}</h1><p className="staff-muted">{fields.given_name} {fields.family_name} · {fields.birth_date}</p></div></header>{intake.source === 'ecw_lookup' && intake.fields.ecw_patient_id && <section className="staff-panel"><h2>Verified in eClinicalWorks</h2><p className="staff-muted">{ecwMatch ? `${ecwMatch.patient.given_name} ${ecwMatch.patient.family_name} · ${ecwMatch.patient.birth_date}` : 'The patient identity was verified in eCW. Appointments will refresh after you confirm the clinic record.'}</p>{ecwMatch?.patient.ecw_account_number && <p><strong>eCW ACCT: {ecwMatch.patient.ecw_account_number}</strong></p>}{ecwMatch && (ecwMatch.appointments.length ? <p className="staff-small">Next scheduled visit: {dateLabel(ecwMatch.appointments[0].start)}{ecwMatch.appointments[0].reason ? ` · ${ecwMatch.appointments[0].reason}` : ''}</p> : <p className="staff-alert staff-warning">eCW found this patient but returned no upcoming appointment. An estimate needs a scheduled visit.</p>)}</section>}<form className="staff-panel" onSubmit={savePatient}><h2>{candidates.length ? 'Possible clinic records' : intake.source === 'ecw_lookup' ? 'No ClinicForce record linked yet' : intake.expected_patient_id ? 'Patient requested by the clinic' : usesAutomaticRecord ? 'Clinic record created from this upload' : 'No matching clinic patient found'}</h2><p className="staff-muted">{intake.source === 'ecw_lookup' ? 'Choose the correct ClinicForce record for the patient found in eCW. This does not create a new eCW patient.' : candidates.length ? 'Compare the details with the patient. Similar records are not linked automatically.' : 'Save this patient to your clinic. This does not create a patient login or an eClinicalWorks record.'}</p>{candidates.map(patient => <label className="staff-match-option" key={patient.id}><input type="radio" name="patient-match" value={patient.id} checked={match === patient.id} onChange={() => setMatch(patient.id)} disabled={Boolean(busy)} required /><span><strong>{patient.given_name} {patient.family_name}</strong><small>Date of birth: {patient.birth_date}</small><small>Clinic record: {patient.id}</small></span></label>)}<label className="staff-match-option"><input type="radio" name="patient-match" value="new" checked={match === 'new'} onChange={() => setMatch('new')} disabled={Boolean(busy)} required /><span><strong>{intake.expected_patient_id ? 'Use the patient this link was requested for' : usesAutomaticRecord ? 'Confirm the clinic record created from this upload' : intake.source === 'ecw_lookup' ? 'Create a ClinicForce record for this eCW patient' : 'Create a new clinic patient record'}</strong><small>{intake.expected_patient_id ? 'The submitted name and date of birth must match the requested patient.' : usesAutomaticRecord ? 'Confirm this record without creating a duplicate patient or a patient login.' : 'Keep this intake separate from existing patients.'}</small></span></label><label className="staff-confirmation"><input type="checkbox" checked={identityConfirmed} onChange={event => setIdentityConfirmed(event.target.checked)} required disabled={Boolean(busy)} /><span>I confirmed the patient's name, date of birth, and selected record.</span></label><div className="staff-actions"><button type="button" className="staff-text-button" onClick={() => { if (intake.source === 'ecw_lookup') retryEcwLookup(intake); else navigate('review') }} disabled={Boolean(busy)}><ArrowLeft size={18} /> {intake.source === 'ecw_lookup' ? 'Change eCW search' : 'Edit details'}</button><button className="staff-primary" disabled={Boolean(busy) || !identityConfirmed || !match}><Check size={18} /> {intake.source === 'ecw_lookup' ? 'Continue to eCW appointment' : 'Save patient and continue'}</button></div></form></div>}
      {screen === 'record' && intake && <>
        <button className="staff-text-button" onClick={() => { navigate(recordBack); if (recordBack === 'inbox') void perform('refresh', refreshInbox) }} disabled={Boolean(busy)}><ArrowLeft size={18} /> {recordBack === 'patients' ? 'Clinical patients' : 'Insurance inbox'}</button><header className="staff-header"><div><div className="staff-eyebrow">Patient insurance workspace</div><h1 tabIndex={-1} ref={heading}>{patientName(intake)}</h1><p className="staff-muted">{intake.fields.payer || 'Insurance company needs review'} · Received {dateLabel(intake.created_at)}</p></div><span className="staff-status">{statusLabel(intake.status)}</span></header>{intake.source === 'patient_upload' && <p className="staff-alert staff-warning">Patient-submitted card and details{intake.patient ? ' · Confirmed by clinic staff.' : ' · Awaiting staff identity confirmation.'}</p>}{!intake.patient && intake.expected_patient && <p className="staff-alert staff-warning">Requested for {intake.expected_patient.given_name} {intake.expected_patient.family_name} · Date of birth: {intake.expected_patient.birth_date}. Confirm the uploaded details match.</p>}{intake.patient && <div className="staff-actions"><button onClick={() => { setLinkBack('record'); setLinkPatient({ id: intake.patient!.id, name: patientName(intake) }); navigate('links') }}>Request an updated insurance card</button></div>}<nav className="staff-tabs" aria-label="Patient insurance sections">{(['overview', 'documents', 'activity'] as const).map(value => <button key={value} onClick={() => setTab(value)} aria-current={tab === value ? 'page' : undefined}>{value === 'overview' ? 'Overview' : value === 'documents' ? 'Documents' : 'Activity'}</button>)}</nav>
        {tab === 'overview' && <><div className="staff-two-columns"><section className="staff-panel"><h2>{intake.patient ? 'Continue insurance review' : 'Confirm this patient first'}</h2><p className="staff-muted">{intake.patient ? 'Choose the information available for this visit. Each path stays attached to this patient intake.' : 'Confirm the name and date of birth before linking patient details or opening the insurance workflow.'}</p>{intake.patient ? <div className="staff-workflow-actions">{intake.source !== 'ecw_lookup' && <button className="staff-primary" onClick={() => onOpenInsurance(intake.id, 'v1')}>Review uploaded documents <ChevronRight size={18} /></button>}{intake.source === 'ecw_lookup' && !intake.fields.ecw_patient_id ? <button className="staff-primary" onClick={() => retryEcwLookup(intake)}>Search this patient in eCW <ChevronRight size={18} /></button> : <button onClick={() => onOpenInsurance(intake.id, 'commercial')}>Check visit estimate <ChevronRight size={18} /></button>}</div> : <button className="staff-primary" onClick={() => editPatient(intake)}>Confirm patient details</button>}<p className="staff-small staff-muted">Visit estimates use eClinicalWorks appointments, Stedi benefits, and your clinic’s contracted rates.</p></section><section className="staff-panel"><h2>Patient and policy</h2><InsuranceCards key={intake.id} intakeId={intake.id} /><dl className="staff-facts"><div><dt>Date of birth</dt><dd>{intake.patient?.birth_date || intake.fields.birth_date || 'Not confirmed'}</dd></div><div><dt>Member ID</dt><dd>{intake.fields.member_id || 'Not confirmed'}</dd></div><div><dt>Policyholder</dt><dd>{intake.fields.subscriber_name || intake.fields.member_name || 'Not confirmed'}</dd></div><div><dt>Relationship</dt><dd>{intake.fields.relationship || 'Not confirmed'}</dd></div></dl><p className="staff-small staff-muted">Saved to {session.practice_name}. A patient account is optional.</p></section></div><section className="staff-panel"><h2>Insurance information sources</h2><div className="staff-document"><span><FileText size={18} /> {intake.source === 'clinicforce_qr' ? 'ClinicForce QR intake' : 'Original card files'}</span><span className="staff-small">{intake.documents.length} card files saved · {dateLabel(intake.created_at)}</span></div><p className="staff-small staff-muted">Uploaded benefits PDFs, clinic fee schedules, and insurance contracts are reviewed in the document workflow. Coverage, authorization, and estimates retain their source information.</p>{intake.warning && <div className="staff-alert staff-warning">{intake.warning}</div>}</section><section className="staff-panel"><h2>Appointment history</h2><p className="staff-muted">Previous appointments and their saved estimates are in this patient’s clinical record.</p>{intake.patient && <button onClick={() => { navigate('patients'); setHistoryPatientId(intake.patient!.id) }}>View patient appointments <ChevronRight size={17} /></button>}</section></>}
        {tab === 'documents' && <><section className="staff-panel"><h2>Original insurance card</h2><p className="staff-muted">{intake.documents.length ? intake.source === 'patient_upload' ? 'Original files submitted by the patient or their representative.' : 'Files captured or uploaded by clinic staff.' : 'This intake started from a ClinicForce QR. No separate card file has been uploaded.'}</p>{intake.documents.map(doc => <div className="staff-document" key={doc.id}><span><FileText size={18} /><span><strong>{doc.filename}</strong><small>{dateLabel(doc.created_at)} · {Math.max(1, Math.round(doc.size_bytes / 1024))} KB</small></span></span><button disabled={Boolean(busy)} onClick={() => void perform('download', () => downloadStaffDocument(intake.id, doc.id, doc.filename))}>Download</button></div>)}{intake.patient && <div className="staff-actions"><button onClick={() => onOpenInsurance(intake.id, 'v1')}>Open document review <ChevronRight size={18} /></button></div>}</section><section className="staff-panel"><h2>Supporting insurance documents</h2><p className="staff-muted">Benefits, contracts, fees, and supporting files saved during this patient’s insurance review.</p>{intake.workflow_documents?.length ? intake.workflow_documents.map(doc => <div className="staff-document" key={doc.id}><span><FileText size={18} /><span><strong>{doc.filename}</strong><small>{doc.kind.replaceAll('_', ' ')} · {doc.version === 'v1' ? 'Document review' : 'Connected insurance'}</small><small>{doc.observed_at ? `Source recorded ${dateLabel(doc.observed_at)}` : 'Source date unavailable'}</small></span></span><button disabled={Boolean(busy)} onClick={() => void perform('download', () => downloadStaffWorkflowDocument(intake.id, doc))}>Download</button></div>) : <p className="staff-small staff-muted">No supporting documents have been saved in this intake’s insurance workflows yet.</p>}</section></>}
        {tab === 'activity' && <section className="staff-panel"><h2>Insurance activity</h2>{intake.events?.length ? <ol className="staff-timeline">{intake.events.map(event => <li key={event.id}><strong>{event.action.replaceAll('_', ' ')}</strong><small>{dateLabel(event.created_at)}{event.actor ? ` · ${event.actor}` : ''}</small></li>)}</ol> : <p className="staff-muted">No additional activity has been recorded.</p>}</section>}
      </>}
      {screen === 'library' && <><header className="staff-header"><div><div className="staff-eyebrow">Practice references</div><h1 tabIndex={-1} ref={heading}>Clinic library</h1><p className="staff-muted">Insurance contracts, fee schedules, self-pay rates, and final ERA payments for {session.practice_name}.</p></div><button onClick={openLibrary} disabled={Boolean(busy)}><RefreshCw size={18} /> Refresh</button></header><section className="staff-panel"><div className="staff-section-heading"><h2>Saved clinic documents</h2>{library !== null && <span className="staff-small staff-muted">{library.length} {library.length === 1 ? 'document' : 'documents'}</span>}</div><p className="staff-small staff-muted">Only documents uploaded to {session.practice_name} appear here. Open a file in a new tab or download a copy.</p><p className="staff-small staff-muted">Staff select the applicable document and version during insurance review. A matching payer name does not apply a contract automatically.</p>{library === null ? <p className="staff-muted" role="status">{busy === 'library' ? 'Loading clinic documents…' : 'Clinic documents have not been loaded. Select Refresh to try again.'}</p> : !library.length ? <div className="staff-empty"><FolderOpen size={30} aria-hidden="true" /><h3>No clinic documents yet</h3><p>{session.role === 'admin' ? 'Add a contract, fee schedule, self-pay rates, or ERA below.' : 'Your clinic administrator can add contracts, fee schedules, self-pay rates, and ERAs.'}</p></div> : library.map(doc => <div className="staff-document" key={doc.id}><span><FileText size={18} /><span><strong>{doc.label}</strong><small>{doc.kind === 'contract' ? 'Payer contract' : doc.kind === 'self_pay' ? 'Self-pay rates' : doc.kind === 'era_835' ? '835 ERA — final adjudication' : 'Fee schedule'}{doc.payer ? ` · ${doc.payer}` : ''}</small><small>{doc.filename} · Added {dateLabel(doc.created_at)}</small><small>Effective {doc.effective_from || 'date not specified'}{doc.effective_to ? ` to ${doc.effective_to}` : ''}</small></span></span><div className="staff-inline-actions"><a className="staff-mail-link" href={`/api/staff/library/${encodeURIComponent(doc.id)}/document?preview=true`} target="_blank" rel="noopener noreferrer" aria-label={`Open ${doc.label} in a new tab`}><ExternalLink size={16} aria-hidden="true" /> Open</a>{doc.kind === 'era_835' && <button disabled={Boolean(busy)} onClick={() => void perform('era', async () => { setEra(await getStaffEra835(doc.id)) })}>View final payment</button>}<button disabled={Boolean(busy)} onClick={() => void perform('download', () => downloadStaffLibraryDocument(doc.id, doc.filename))}>Download</button></div></div>)}</section>{era && <section className="staff-panel"><h2>Final 835 payment</h2><p className="staff-muted">{era.payer || 'Payer not named'} · {era.payment_date || 'Payment date not supplied'} · Total payment {money(era.payment_cents)} · {era.claim_count} claim{era.claim_count === 1 ? '' : 's'}</p><div className="staff-facts">{era.claims.map(claim => <div key={`${claim.patient_control_number}:${claim.payer_claim_control_number}`}><strong>{claim.patient_control_number}</strong><small>Insurer paid {money(claim.insurer_paid_cents)} · Final patient responsibility {money(claim.patient_responsibility_cents)} · Billed {money(claim.billed_cents)}</small></div>)}</div><p className="staff-small staff-muted">Match the patient-control number to the submitted claim before posting. This is final adjudication evidence, not the pre-service PCE estimate.</p></section>}{session.role === 'admin' && <form className="staff-panel" onSubmit={event => { event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); const from = String(data.get('effective_from') || ''); const to = String(data.get('effective_to') || ''); if (from && to && to < from) { setError('The effective end date must be on or after the start date.'); return } void perform('library-upload', async () => { const document = await uploadStaffLibrary(data); setLibrary(current => [document, ...(current || [])]); form.reset(); setNotice('Clinic document saved. Staff can select this version during insurance review.') }) }}><h2>Add a clinic document</h2><div className="staff-two-columns"><label className="staff-field"><span>Document type</span><select name="kind" disabled={Boolean(busy)}><option value="fee_schedule">Fee schedule</option><option value="contract">Payer contract</option><option value="self_pay">Self-pay rates</option><option value="era_835">835 ERA</option></select></label><label className="staff-field"><span>Label *</span><input name="label" required placeholder="2026 office fee schedule" disabled={Boolean(busy)} /></label><label className="staff-field"><span>Payer, if applicable</span><input name="payer" disabled={Boolean(busy)} /></label><label className="staff-field"><span>File *</span><input type="file" name="file" accept=".csv,.pdf,.835,.edi,.x12,.txt,application/pdf,text/csv,text/plain" required disabled={Boolean(busy)} /><small>PDF for contracts; CSV for rates; original X12 file for an 835 ERA.</small></label><label className="staff-field"><span>Effective from</span><input name="effective_from" type="date" disabled={Boolean(busy)} /></label><label className="staff-field"><span>Effective to</span><input name="effective_to" type="date" disabled={Boolean(busy)} /></label></div><button className="staff-primary" disabled={Boolean(busy)}><Upload size={18} /> Save clinic document</button></form>}</>}
      {screen === 'members' && session.role === 'admin' && <div className="staff-narrow"><header className="staff-header"><div><div className="staff-eyebrow">Clinic administration</div><h1 tabIndex={-1} ref={heading}>Staff accounts and invitations</h1><p className="staff-muted">Grant colleagues access to the insurance inbox for {session.practice_name}. Invite them to choose their own password, or create an account below.</p></div></header><form className="staff-panel" onSubmit={event => { event.preventDefault(); const role = new FormData(event.currentTarget).get('role') === 'admin' ? 'admin' : 'staff'; void perform('invitation', async () => { setInvitation(await createStaffInvitation(role)) }) }}><h2>Invite a colleague</h2><label className="staff-field"><span>Invitation access level</span><select name="role" disabled={Boolean(busy)}><option value="staff">Staff · Patient insurance</option><option value="admin">Administrator · Patient insurance and staff accounts</option></select></label><button className="staff-primary" disabled={Boolean(busy)}>Create staff invitation</button></form>{invitation && <section className="staff-panel"><StaffShareLink key={invitation.id} kind="invitations" id={invitation.id} url={invitation.url} expiresAt={invitation.expires_at} clinicName={session.practice_name} /></section>}<form className="staff-panel" onSubmit={event => { event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); void perform('member', async () => { await addStaffMember({ username: String(data.get('username')).trim(), display_name: String(data.get('display_name')).trim(), password: String(data.get('password')), role: data.get('role') === 'admin' ? 'admin' : 'staff' }); form.reset(); setNotice('Staff account created. Share the credentials through your clinic’s approved channel.') }) }}><h2>Create a staff account directly</h2><label className="staff-field"><span>Display name</span><input name="display_name" required disabled={Boolean(busy)} /></label><label className="staff-field"><span>Username</span><input name="username" autoComplete="off" required disabled={Boolean(busy)} /></label><label className="staff-field"><span>Password</span><input name="password" type="password" autoComplete="new-password" minLength={12} required disabled={Boolean(busy)} /><small>Use at least 12 characters.</small></label><label className="staff-field"><span>Access level</span><select name="role" disabled={Boolean(busy)}><option value="staff">Staff · Patient insurance</option><option value="admin">Administrator · Patient insurance and staff accounts</option></select></label><button className="staff-primary" disabled={Boolean(busy)}><Plus size={18} /> Create staff account</button></form><section className="staff-panel"><div className="staff-section-heading"><h2>Staff invitations</h2><button disabled={Boolean(busy)} onClick={() => void perform('refresh-invitations', async () => { setInvitations((await getStaffInvitations()).invitations) })}><RefreshCw size={17} /> Refresh invitations</button></div>{invitations === null ? <p className="staff-muted">Invitations have not loaded.</p> : invitations.length === 0 ? <p className="staff-muted">No invitations created yet.</p> : invitations.map(item => <div className="staff-document" key={item.id}><span><span><strong>{item.role === 'admin' ? 'Administrator' : 'Staff'} invitation · {item.status}</strong><small>Created {dateLabel(item.created_at)} · Expires {dateLabel(item.expires_at)}</small></span></span>{item.status === 'open' && <button disabled={Boolean(busy)} onClick={() => void perform('revoke-invitation', async () => { await revokeStaffInvitation(item.id); if (invitation?.id === item.id) setInvitation(null); setInvitations((await getStaffInvitations()).invitations); setNotice('Staff invitation revoked.') })}>Revoke invitation</button>}</div>)}</section></div>}
    </div>
  </section>
}
