import * as Dialog from '@radix-ui/react-dialog'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import {
  ArrowLeft,
  Building2,
  CheckCircle2,
  ChevronRight,
  ClipboardPlus,
  Cloud,
  Download,
  Eye,
  EyeOff,
  FileText,
  FolderOpen,
  KeyRound,
  LockKeyhole,
  LoaderCircle,
  LogOut,
  Plus,
  QrCode,
  RefreshCw,
  ShieldCheck,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import {
  ApiError,
  beginGoogleDriveConnection,
  beginEpicAuthorization,
  beginGoogleSignIn,
  chooseStorageProvider,
  deletePatientDocument,
  deletePatientDocuments,
  disconnectEpic,
  downloadEpicDocument,
  downloadEpicReport,
  generateEpicLink,
  getEpicRecord,
  getEpicShares,
  downloadPatientDocument,
  downloadPatientDocumentsArchive,
  generatePatientLink,
  getCurrentPatientSession,
  loginPatient,
  importGoogleDriveChanges,
  logoutPatient,
  registerPatient,
  revokeEpicShare,
  searchEpicEndpoints,
  savePatientRecord,
  type LinkResponse,
  type EpicEndpoint,
  type EpicRecord,
  type EpicShare,
  type ManualRecordInput,
  type PatientDocument,
  type PatientSession,
  type RecordCategory,
  uploadPatientPdfs,
} from '../services/api'

interface PatientPortalProps {
  onBack: () => void
}

type AuthMode = 'login' | 'register'
type FormState = Record<Exclude<keyof ManualRecordInput, 'category'>, string>

const emptyForm: FormState = {
  hospital: '', name: '', value: '', unit: '', date: '', details: '', dosage: '',
}

const categoryMeta: Record<RecordCategory, { label: string; help: string; icon: string }> = {
  hospital: { label: 'Care visit', help: 'A hospital, clinic, or care-team visit', icon: '01' },
  lab: { label: 'Lab result', help: 'A test name and result', icon: '02' },
  vital: { label: 'Vital sign', help: 'A measurement such as blood pressure', icon: '03' },
  condition: { label: 'Condition', help: 'A diagnosis or ongoing condition', icon: '04' },
  allergy: { label: 'Allergy', help: 'An allergy or intolerance', icon: '05' },
  medication: { label: 'Medication', help: 'A medication you currently take', icon: '06' },
}

function dateLabel(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function patientInitials(patientId: string): string {
  return patientId.slice(0, 2).toUpperCase() || 'PT'
}

function messageFrom(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

export default function PatientPortal({ onBack }: PatientPortalProps) {
  const [mode, setMode] = useState<AuthMode>('login')
  const [patientId, setPatientId] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [session, setSession] = useState<PatientSession | null>(null)
  const [files, setFiles] = useState<File[]>([])
  const [category, setCategory] = useState<RecordCategory>('hospital')
  const [form, setForm] = useState<FormState>(emptyForm)
  const [sharePasscode, setSharePasscode] = useState('')
  const [epicSharePasscode, setEpicSharePasscode] = useState('')
  const [link, setLink] = useState<LinkResponse | null>(null)
  const [epicRecord, setEpicRecord] = useState<EpicRecord | null>(null)
  const [epicEndpoints, setEpicEndpoints] = useState<EpicEndpoint[]>([])
  const [epicQuery, setEpicQuery] = useState('')
  const [epicShares, setEpicShares] = useState<EpicShare[]>([])
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState<'auth' | 'upload' | 'record' | 'share' | 'download' | 'delete' | 'bulk-download' | 'bulk-delete' | 'import' | 'drive' | 'google' | 'epic-search' | 'epic-connect' | 'epic-load' | 'epic-share' | 'epic-report' | 'epic-document' | 'epic-revoke' | null>(null)
  const [restoringSession, setRestoringSession] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let active = true
    void getCurrentPatientSession()
      .then((workspace) => {
        if (active) {
          setSession(workspace)
          setNotice(workspace.drive_sync_message ?? 'Your Google Drive workspace is ready.')
          if (new URLSearchParams(window.location.search).get('epic') === 'connected') {
            void getEpicRecord().then((record) => {
              if (!active) return
              setEpicRecord(record)
              setNotice(`Live Epic record connected from ${record.source.organization_name}.`)
              void getEpicShares().then((shares) => { if (active) setEpicShares(shares) })
            }).catch((caught) => { if (active) setError(messageFrom(caught, 'Epic connected, but the live record could not be loaded.')) })
            window.history.replaceState({}, '', window.location.pathname)
          }
        }
      })
      .catch((caught) => {
        if (active && !(caught instanceof ApiError && caught.status === 401)) {
          setError(messageFrom(caught, 'Unable to restore your workspace.'))
        }
      })
      .finally(() => { if (active) setRestoringSession(false) })
    return () => { active = false }
  }, [])

  const setField = (key: keyof FormState, value: string) => setForm((current) => ({ ...current, [key]: value }))
  const clearFeedback = () => { setError(''); setNotice('') }

  const authenticate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const normalizedId = patientId.trim()
    if (!normalizedId || !password) {
      setError('Enter both your patient ID and password.')
      return
    }
    if (mode === 'register' && password.length < 12) {
      setError('Use a password with at least 12 characters.')
      return
    }
    clearFeedback()
    setBusy('auth')
    try {
      setSession(mode === 'login' ? await loginPatient(normalizedId, password) : await registerPatient(normalizedId, password))
      setNotice(mode === 'login' ? 'Your workspace is ready.' : 'Your account is ready. Add a report or health detail to get started.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to open your workspace.'))
    } finally {
      setBusy(null)
    }
  }

  const chooseFiles = (nextFiles: File[]) => {
    const invalid = nextFiles.find((file) => !file.name.toLowerCase().endsWith('.pdf'))
    if (invalid) {
      setFiles([])
      if (fileInput.current) fileInput.current.value = ''
      setError(`${invalid.name} is not a PDF. Choose PDF reports only.`)
      return
    }
    const oversized = nextFiles.find((file) => file.size > 15 * 1024 * 1024)
    if (oversized) {
      setFiles([])
      if (fileInput.current) fileInput.current.value = ''
      setError(`${oversized.name} exceeds the 15 MB limit.`)
      return
    }
    clearFeedback()
    setFiles(nextFiles)
  }

  const upload = async () => {
    if (!session || !files.length) {
      setError('Choose at least one PDF report before uploading.')
      return
    }
    clearFeedback()
    setBusy('upload')
    try {
      setSession(await uploadPatientPdfs(session.patient_id, password, files))
      setFiles([])
      if (fileInput.current) fileInput.current.value = ''
      setNotice(`${files.length === 1 ? 'Report' : `${files.length} reports`} added to ${session.storage_provider === 'google_drive' ? 'your Google Drive folder' : 'your secure workspace'}.`)
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to upload the selected reports.'))
    } finally {
      setBusy(null)
    }
  }

  const connectGoogleDrive = async () => {
    if (!session) return
    clearFeedback()
    setBusy('drive')
    try {
      window.location.assign(await beginGoogleDriveConnection(session.patient_id, password))
    } catch (caught) {
      setBusy(null)
      setError(messageFrom(caught, 'Unable to start the Google Drive connection.'))
    }
  }

  const signInWithGoogle = async () => {
    clearFeedback()
    setBusy('google')
    try {
      window.location.assign(await beginGoogleSignIn())
    } catch (caught) {
      setBusy(null)
      setError(messageFrom(caught, 'Unable to start Google sign-in.'))
    }
  }

  const selectStorage = async (storageProvider: 's3' | 'google_drive') => {
    if (!session) return
    if (storageProvider === 'google_drive' && !session.drive_connected) {
      await connectGoogleDrive()
      return
    }
    clearFeedback()
    setBusy('drive')
    try {
      setSession(await chooseStorageProvider(session.patient_id, password, storageProvider))
      setNotice(storageProvider === 'google_drive' ? 'New reports will be stored in your ClinicForce Google Drive folder.' : 'New reports will be stored in ClinicForce secure storage.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to change document storage.'))
    } finally {
      setBusy(null)
    }
  }

  const recordPayload = (): ManualRecordInput | null => {
    const trimmed = Object.fromEntries(Object.entries(form).map(([key, value]) => [key, value.trim()])) as FormState
    if (category === 'hospital' && !trimmed.hospital) {
      setError('Enter the hospital or clinic for this care visit.')
      return null
    }
    if ((category === 'lab' || category === 'vital') && (!trimmed.name || !trimmed.value)) {
      setError('Enter both a measurement name and its result.')
      return null
    }
    if (['condition', 'allergy', 'medication'].includes(category) && !trimmed.name) {
      setError(`Enter the ${categoryMeta[category].label.toLowerCase()} name.`)
      return null
    }
    return { category, ...trimmed }
  }

  const saveRecord = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!session) return
    const payload = recordPayload()
    if (!payload) return
    clearFeedback()
    setBusy('record')
    try {
      setSession(await savePatientRecord(session.patient_id, password, payload))
      setForm(emptyForm)
      setNotice('Health detail added to your workspace.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to save this health detail.'))
    } finally {
      setBusy(null)
    }
  }

  const generateLink = async () => {
    if (!session) return
    if (!/^\d{4}$/.test(sharePasscode)) {
      setError('Choose a four-digit passcode for the clinician.')
      return
    }
    if (!session.documents.length && !session.records.length) {
      setError('Add a PDF report or health detail before creating a share link.')
      return
    }
    clearFeedback()
    setBusy('share')
    try {
      setLink(await generatePatientLink(session.patient_id, password, sharePasscode))
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to generate the secure link.'))
    } finally {
      setBusy(null)
    }
  }

  const searchEpic = async (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault()
    if (epicQuery.trim().length < 2) {
      setError('Enter at least two letters of your hospital or health system name.')
      return
    }
    clearFeedback()
    setBusy('epic-search')
    try {
      setEpicEndpoints(await searchEpicEndpoints(epicQuery))
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to search the Epic hospital directory.'))
    } finally {
      setBusy(null)
    }
  }

  const connectEpic = async (endpoint: EpicEndpoint) => {
    if (!session) return
    clearFeedback()
    setBusy('epic-connect')
    try {
      window.location.assign(await beginEpicAuthorization(session.patient_id, password, endpoint.id))
    } catch (caught) {
      setBusy(null)
      setError(messageFrom(caught, 'Unable to start the Epic connection.'))
    }
  }

  const refreshEpic = async () => {
    clearFeedback()
    setBusy('epic-load')
    try {
      const record = await getEpicRecord()
      setEpicRecord(record)
      setNotice(`Live Epic data refreshed from ${record.source.organization_name}.`)
    } catch (caught) {
      setEpicRecord(null)
      setError(messageFrom(caught, 'Your Epic session needs to be reconnected.'))
    } finally {
      setBusy(null)
    }
  }

  const endEpicSession = async () => {
    clearFeedback()
    try {
      await disconnectEpic()
      setEpicRecord(null)
      setEpicEndpoints([])
      setNotice('Epic disconnected. ClinicForce no longer has live access to this record.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to end the Epic connection.'))
    }
  }

  const generateEpicShare = async () => {
    if (!session || !/^\d{4}$/.test(epicSharePasscode)) {
      setError('Choose a four-digit passcode for the Epic snapshot.')
      return
    }
    clearFeedback()
    setBusy('epic-share')
    try {
      setLink(await generateEpicLink(session.patient_id, password, epicSharePasscode))
      setEpicShares(await getEpicShares())
      setNotice('A timestamped Epic snapshot was encrypted for sharing. It excludes Epic PDF attachments.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to create the Epic share snapshot.'))
    } finally {
      setBusy(null)
    }
  }

  const downloadLiveEpicReport = async () => {
    if (!session) return
    clearFeedback()
    setBusy('epic-report')
    try {
      const url = URL.createObjectURL(await downloadEpicReport(session.patient_id, password))
      const linkElement = document.createElement('a')
      linkElement.href = url
      linkElement.download = 'clinicforce-epic-live-record.pdf'
      document.body.appendChild(linkElement)
      linkElement.click()
      linkElement.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to create the live Epic PDF.'))
    } finally {
      setBusy(null)
    }
  }

  const openEpicDocument = async (documentId: string) => {
    clearFeedback()
    setBusy('epic-document')
    try {
      const url = URL.createObjectURL(await downloadEpicDocument(documentId))
      window.open(url, '_blank', 'noopener,noreferrer')
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to open this live Epic PDF.'))
    } finally {
      setBusy(null)
    }
  }

  const removeEpicShare = async (manifestId: string) => {
    clearFeedback()
    setBusy('epic-revoke')
    try {
      await revokeEpicShare(manifestId)
      setEpicShares((shares) => shares.filter((share) => share.manifest_id !== manifestId))
      setNotice('Epic share snapshot revoked immediately.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to revoke this Epic snapshot.'))
    } finally {
      setBusy(null)
    }
  }

  const openDocument = async (document: PatientDocument) => {
    if (!session) return
    clearFeedback()
    setBusy('download')
    try {
      const url = URL.createObjectURL(await downloadPatientDocument(session.patient_id, password, document.id))
      window.open(url, '_blank', 'noopener,noreferrer')
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to open this report.'))
    } finally {
      setBusy(null)
    }
  }

  const importDriveChanges = async () => {
    if (!session) return
    clearFeedback()
    setBusy('import')
    try {
      const workspace = await importGoogleDriveChanges(session.patient_id, password)
      setSession(workspace)
      setNotice(workspace.drive_sync_message ?? 'Drive changes were imported securely.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to import the detected Drive files.'))
    } finally {
      setBusy(null)
    }
  }

  const deleteDocument = async (document: PatientDocument) => {
    if (!session || !window.confirm(`Delete ${document.filename}? This removes it from ClinicForce and its selected storage.`)) return
    clearFeedback()
    setBusy('delete')
    try {
      const workspace = await deletePatientDocument(session.patient_id, password, document.id)
      setSession(workspace)
      setSelectedDocumentIds((current) => {
        const next = new Set(current)
        next.delete(document.id)
        return next
      })
      setNotice(workspace.drive_sync_message ?? 'Report deleted.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to delete this report.'))
    } finally {
      setBusy(null)
    }
  }

  const toggleDocumentSelection = (documentId: number) => {
    setSelectedDocumentIds((current) => {
      const next = new Set(current)
      if (next.has(documentId)) next.delete(documentId)
      else next.add(documentId)
      return next
    })
  }

  const toggleAllDocumentSelection = () => {
    if (!session) return
    setSelectedDocumentIds((current) => current.size === session.documents.length
      ? new Set()
      : new Set(session.documents.map((document) => document.id)))
  }

  const downloadSelectedDocuments = async () => {
    if (!session || selectedDocumentIds.size === 0) return
    clearFeedback()
    setBusy('bulk-download')
    try {
      const archive = await downloadPatientDocumentsArchive(session.patient_id, password, Array.from(selectedDocumentIds))
      const url = URL.createObjectURL(archive)
      const linkElement = document.createElement('a')
      linkElement.href = url
      linkElement.download = 'clinicforce-reports.zip'
      document.body.appendChild(linkElement)
      linkElement.click()
      linkElement.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
      setNotice(`${selectedDocumentIds.size} selected report${selectedDocumentIds.size === 1 ? '' : 's'} downloaded as a ZIP file.`)
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to download the selected reports.'))
    } finally {
      setBusy(null)
    }
  }

  const deleteSelectedDocuments = async () => {
    if (!session || selectedDocumentIds.size === 0) return
    const count = selectedDocumentIds.size
    if (!window.confirm(`Delete ${count} selected report${count === 1 ? '' : 's'}? This removes them from ClinicForce and their selected storage.`)) return
    clearFeedback()
    setBusy('bulk-delete')
    try {
      const workspace = await deletePatientDocuments(session.patient_id, password, Array.from(selectedDocumentIds))
      setSession(workspace)
      setSelectedDocumentIds(new Set())
      setNotice(workspace.drive_sync_message ?? 'Selected reports deleted.')
    } catch (caught) {
      setError(messageFrom(caught, 'Unable to delete the selected reports.'))
    } finally {
      setBusy(null)
    }
  }

  const signOut = () => {
    void logoutPatient()
    setSession(null)
    setPassword('')
    setFiles([])
    setLink(null)
    setEpicRecord(null)
    setEpicShares([])
    setSelectedDocumentIds(new Set())
    setSharePasscode('')
    clearFeedback()
  }

  if (!session) {
    return (
      <section className="auth-layout animate-enter" aria-labelledby="patient-heading">
        <button className="back-control" type="button" onClick={onBack}><ArrowLeft aria-hidden="true" className="size-4" /> Back to home</button>
        <div className="auth-aside">
          <p className="eyebrow"><ShieldCheck aria-hidden="true" className="size-4" /> Patient workspace</p>
          <h1 id="patient-heading">Build a record you can carry into care.</h1>
          <p className="lead-small">Add source reports and the health details you know. When you are ready, create a QR code for your clinician to scan.</p>
          <div className="auth-note"><LockKeyhole aria-hidden="true" /><p><strong>You remain in control.</strong><br />Share only after you choose a passcode and show the QR code.</p></div>
        </div>
        <div className="panel auth-panel">
          <div className="mode-tabs" role="tablist" aria-label="Patient account options">
            <button className={mode === 'login' ? 'active' : ''} role="tab" aria-selected={mode === 'login'} type="button" onClick={() => { setMode('login'); clearFeedback() }}>Sign in</button>
            <button className={mode === 'register' ? 'active' : ''} role="tab" aria-selected={mode === 'register'} type="button" onClick={() => { setMode('register'); clearFeedback() }}>Create account</button>
          </div>
          <h2>{mode === 'login' ? 'Welcome back' : 'Create your workspace'}</h2>
          <p className="text-sm leading-6 text-[var(--muted-ink)]">{mode === 'login' ? 'Enter your account details to open your health record.' : 'Use a memorable patient ID and a strong password.'}</p>
          {restoringSession ? <div className="status-panel"><LoaderCircle className="size-5 animate-spin" aria-hidden="true" /><p>Opening your secure workspace…</p></div> : <form className="stack-form" onSubmit={authenticate} noValidate>
            <label htmlFor="patient-id">Patient ID</label>
            <input id="patient-id" autoComplete="username" value={patientId} onChange={(event) => setPatientId(event.target.value)} placeholder="e.g. alex-morgan" />
            <label htmlFor="patient-password">Password</label>
            <div className="password-field">
              <input id="patient-password" type={showPassword ? 'text' : 'password'} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} value={password} onChange={(event) => setPassword(event.target.value)} aria-describedby={mode === 'register' ? 'password-help' : undefined} />
              <button className="icon-button" type="button" onClick={() => setShowPassword((visible) => !visible)} aria-label={showPassword ? 'Hide password' : 'Show password'}>{showPassword ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}</button>
            </div>
            {mode === 'register' && <p id="password-help" className="field-help">Use at least 12 characters.</p>}
            {error && <p className="form-error" role="alert">{error}</p>}
            <button className="button button-primary w-full" type="submit" disabled={busy === 'auth'}>{busy === 'auth' ? <><LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> Opening workspace</> : <>{mode === 'login' ? 'Open my workspace' : 'Create my workspace'} <ChevronRight aria-hidden="true" className="size-4" /></>}</button>
          </form>}
          {!restoringSession && <><div className="divider"><span>or</span></div><button className="button button-secondary w-full" type="button" disabled={busy === 'google'} onClick={() => void signInWithGoogle()}>{busy === 'google' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Cloud aria-hidden="true" className="size-4" />} Continue with Google</button><p className="field-help">Google sign-in opens your patient workspace and stores new PDFs in your ClinicForce Drive folder.</p></>}
        </div>
      </section>
    )
  }

  const recordsAvailable = session.documents.length + session.records.length
  const needsName = category !== 'hospital'
  const needsResult = category === 'lab' || category === 'vital'
  const epicResources = (epicRecord?.bundle.entry ?? []).map((entry) => entry.resource).filter(Boolean)
  const epicCounts = epicResources.reduce<Record<string, number>>((counts, resource) => {
    const type = typeof resource.resourceType === 'string' ? resource.resourceType : 'FHIR resource'
    counts[type] = (counts[type] ?? 0) + 1
    return counts
  }, {})

  return (
    <section className="workspace animate-enter" aria-labelledby="workspace-heading">
      <div className="workspace-banner">
        <div className="avatar" aria-hidden="true">{patientInitials(session.patient_id)}</div>
        <div><p className="eyebrow">Patient workspace</p><h1 id="workspace-heading">Your health record</h1><p>Signed in as <strong>{session.patient_id}</strong></p></div>
        <button className="button button-ghost signout-button" type="button" onClick={signOut}><LogOut aria-hidden="true" className="size-4" /> Sign out</button>
      </div>

      {(error || notice) && <div className={error ? 'feedback feedback-error' : 'feedback feedback-success'} role={error ? 'alert' : 'status'}>{error ? <X aria-hidden="true" /> : <CheckCircle2 aria-hidden="true" />}<span>{error || notice}</span><button type="button" onClick={clearFeedback} aria-label="Dismiss message"><X aria-hidden="true" className="size-4" /></button></div>}

      <div className="workspace-stats" aria-label="Workspace summary">
        <div><FileText aria-hidden="true" /><span><strong>{session.documents.length}</strong> report{session.documents.length === 1 ? '' : 's'} stored</span></div>
        <div><ClipboardPlus aria-hidden="true" /><span><strong>{session.records.length}</strong> health detail{session.records.length === 1 ? '' : 's'} added</span></div>
        <div><ShieldCheck aria-hidden="true" /><span>{recordsAvailable ? 'Ready to share' : 'Add your first item'}</span></div>
      </div>

      <div className="workspace-grid">
        <div className="workspace-main">
          <section className="section-card" aria-labelledby="epic-heading">
            <div className="section-heading"><div><p className="section-kicker">Live patient-authorized connection</p><h2 id="epic-heading">Epic / MyChart</h2></div><Building2 aria-hidden="true" className="section-icon" /></div>
            {!epicRecord ? <>
              <p className="text-sm leading-6 text-[var(--muted-ink)]">Connect a hospital you have used to view its current Epic record in this browser. ClinicForce does not save Epic records, access tokens, or attached PDFs.</p>
              <form className="storage-choice mt-4" onSubmit={(event) => void searchEpic(event)}>
                <div className="flex-1"><label htmlFor="epic-hospital"><strong>Hospital or health system</strong></label><input id="epic-hospital" value={epicQuery} onChange={(event) => setEpicQuery(event.target.value)} placeholder="e.g. Johns Hopkins, UCLA Health" autoComplete="off" /></div>
                <button className="button button-secondary" type="submit" disabled={busy === 'epic-search'}>{busy === 'epic-search' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Building2 className="size-4" aria-hidden="true" />} Search Epic hospitals</button>
              </form>
              {epicEndpoints.length > 0 && <ul className="item-list mt-4" aria-label="Epic hospital matches">{epicEndpoints.map((endpoint) => <li key={endpoint.id}><div className="file-mark"><Building2 aria-hidden="true" /></div><div className="min-w-0 flex-1"><strong>{endpoint.name}</strong>{endpoint.aliases.length > 0 && <p>{endpoint.aliases.slice(0, 2).join(' · ')}</p>}</div><button className="button button-secondary" type="button" disabled={busy === 'epic-connect'} onClick={() => void connectEpic(endpoint)}>{busy === 'epic-connect' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : 'Connect MyChart'}</button></li>)}</ul>}
              {epicQuery && busy !== 'epic-search' && epicEndpoints.length === 0 && <p className="field-help mt-3">Search the exact hospital or health-system name. Only hospitals enabled in Epic’s patient-facing directory can appear.</p>}
            </> : <>
              <div className="storage-choice mt-4"><div><strong>Live from {epicRecord.source.organization_name}</strong><p>Retrieved {dateLabel(epicRecord.source.retrieved_at)}. This connection ends when you disconnect, its token expires, or the service restarts.</p></div><div className="storage-actions"><button className="button button-secondary" type="button" onClick={() => void refreshEpic()} disabled={busy === 'epic-load'}>{busy === 'epic-load' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <RefreshCw className="size-4" aria-hidden="true" />} Refresh</button><button className="button button-secondary" type="button" onClick={() => void endEpicSession()}>Disconnect</button></div></div>
              <div className="workspace-stats mt-4" aria-label="Live Epic data counts">{Object.entries(epicCounts).slice(0, 6).map(([type, count]) => <div key={type}><FileText aria-hidden="true" /><span><strong>{count}</strong> {type}</span></div>)}</div>
              <div className="storage-actions mt-4"><button className="button button-secondary" type="button" onClick={() => void downloadLiveEpicReport()} disabled={busy === 'epic-report'}>{busy === 'epic-report' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Download className="size-4" aria-hidden="true" />} Download live PDF</button></div>
              <details className="mt-4"><summary className="document-name">More from Epic ({epicResources.length} resources)</summary><p className="field-help mt-2">Availability: {Object.entries(epicRecord.availability).filter(([, value]) => value !== 'ok').map(([type, value]) => `${type}: ${value}`).join(' · ') || 'All requested supported resource families responded.'}</p><div className="record-list mt-3">{epicResources.map((resource, index) => <details key={`${resource.resourceType ?? 'resource'}-${resource.id ?? index}`} className="border-b border-[var(--border)] py-3"><summary><strong>{String(resource.resourceType ?? 'FHIR resource')}</strong>{resource.id ? ` · ${String(resource.id)}` : ''}</summary>{resource.resourceType === 'DocumentReference' && typeof resource.id === 'string' && <button className="button button-secondary mt-3" type="button" disabled={busy === 'epic-document'} onClick={() => void openEpicDocument(resource.id!)}>Open live PDF if Epic provides one</button>}<pre className="mt-3 max-h-80 overflow-auto rounded-md bg-slate-950 p-3 text-xs text-slate-100">{JSON.stringify(resource, null, 2)}</pre></details>)}</div></details>
              <div className="storage-choice mt-4"><div><strong>Create a timestamped Epic snapshot</strong><p>This is the one exception: it encrypts a fresh, bounded FHIR snapshot for a clinician QR share. It never includes Epic attachment files.</p><label className="sr-only" htmlFor="epic-share-passcode">Four-digit Epic snapshot passcode</label><input id="epic-share-passcode" className="mt-2" type="password" inputMode="numeric" maxLength={4} value={epicSharePasscode} onChange={(event) => setEpicSharePasscode(event.target.value.replace(/\D/g, ''))} placeholder="Four-digit passcode" /></div><button className="button button-primary" type="button" disabled={busy === 'epic-share'} onClick={() => void generateEpicShare()}>{busy === 'epic-share' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <QrCode className="size-4" aria-hidden="true" />} Share Epic snapshot</button></div>
              {epicShares.length > 0 && <div className="mt-4"><p className="field-help">Active Epic snapshots (each can be revoked immediately)</p><ul className="item-list">{epicShares.map((share) => <li key={share.manifest_id}><div className="file-mark"><QrCode aria-hidden="true" /></div><div className="min-w-0 flex-1"><strong>Epic snapshot</strong><p>{dateLabel(share.created_at)}</p></div><button className="button button-danger" type="button" disabled={busy === 'epic-revoke'} onClick={() => void removeEpicShare(share.manifest_id)}>{busy === 'epic-revoke' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Trash2 className="size-4" aria-hidden="true" />} Revoke</button></li>)}</ul></div>}
            </>}
          </section>

          <section className="section-card" aria-labelledby="reports-heading">
            <div className="section-heading"><div><p className="section-kicker">Source documents</p><h2 id="reports-heading">PDF reports</h2></div><FileText aria-hidden="true" className="section-icon" /></div>
            <div className="storage-choice" aria-label="Choose where new reports are stored">
              <div><strong>{session.storage_provider === 'google_drive' ? 'Your Google Drive is selected' : 'ClinicForce secure storage is selected'}</strong><p>{session.storage_provider === 'google_drive' ? 'Drive contains encrypted ClinicForce files. Open reports here to read them.' : 'New PDFs stay in ClinicForce encrypted document storage.'}</p></div>
              <div className="storage-actions">
                <button className="button button-secondary" type="button" disabled={busy === 'drive' || session.storage_provider === 's3'} onClick={() => void selectStorage('s3')}>Use ClinicForce storage</button>
                <button className="button button-secondary" type="button" disabled={busy === 'drive' || session.storage_provider === 'google_drive'} onClick={() => void selectStorage('google_drive')}><Cloud aria-hidden="true" className="size-4" /> {session.drive_connected ? 'Use my Drive' : 'Connect Google Drive'}</button>
              </div>
            </div>
            {session.storage_provider === 'google_drive' && session.drive_access_upgrade_required && <div className="feedback feedback-error" role="status"><Cloud aria-hidden="true" /><span>Reconnect Google Drive to enable secure folder import. Your existing reports remain available.</span><button className="button button-secondary" type="button" disabled={busy === 'drive'} onClick={() => void connectGoogleDrive()}>Reconnect Drive</button></div>}
            {session.storage_provider === 'google_drive' && session.drive_pending_import_count > 0 && !session.drive_access_upgrade_required && <div className="feedback feedback-success" role="status"><ShieldCheck aria-hidden="true" /><span>{session.drive_pending_import_count} Drive PDF{session.drive_pending_import_count === 1 ? '' : 's'} need secure import. Plaintext originals will be replaced with encrypted ClinicForce files.</span><button className="button button-secondary" type="button" disabled={busy === 'import'} onClick={() => void importDriveChanges()}>{busy === 'import' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <LockKeyhole aria-hidden="true" className="size-4" />} Import securely</button></div>}
            {session.documents.length ? <>
              <div className="document-selection-toolbar">
                <label className="select-all-control">
                  <input
                    type="checkbox"
                    checked={selectedDocumentIds.size === session.documents.length}
                    onChange={toggleAllDocumentSelection}
                    disabled={busy === 'bulk-download' || busy === 'bulk-delete'}
                  />
                  <span>Select all reports</span>
                </label>
                {selectedDocumentIds.size > 0 && <div className="document-bulk-actions" role="status">
                  <span>{selectedDocumentIds.size} selected</span>
                  <button className="button button-secondary" type="button" onClick={() => void downloadSelectedDocuments()} disabled={busy === 'bulk-download' || busy === 'bulk-delete'}>
                    {busy === 'bulk-download' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Download className="size-4" aria-hidden="true" />} Download ZIP
                  </button>
                  <button className="button button-danger" type="button" onClick={() => void deleteSelectedDocuments()} disabled={busy === 'bulk-download' || busy === 'bulk-delete'}>
                    {busy === 'bulk-delete' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Trash2 className="size-4" aria-hidden="true" />} Delete
                  </button>
                </div>}
              </div>
              <ul className="item-list">{session.documents.map((document) => <li key={document.id}>
                <label className="document-select" aria-label={`Select ${document.filename}`}>
                  <input type="checkbox" checked={selectedDocumentIds.has(document.id)} onChange={() => toggleDocumentSelection(document.id)} disabled={busy === 'bulk-download' || busy === 'bulk-delete'} />
                </label>
                <div className="file-mark"><FileText aria-hidden="true" /></div>
                <div className="min-w-0 flex-1"><button type="button" className="document-name" onClick={() => void openDocument(document)} disabled={busy === 'download'}>{document.filename}</button><p>{dateLabel(document.uploaded_at)} · {document.resource_count} extracted item{document.resource_count === 1 ? '' : 's'}</p></div>
                <button className="item-action" type="button" onClick={() => void openDocument(document)} disabled={busy === 'download'} aria-label={`Open ${document.filename}`}>{busy === 'download' ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Eye aria-hidden="true" />}</button>
                <button className="item-action" type="button" onClick={() => void deleteDocument(document)} disabled={busy === 'delete'} aria-label={`Delete ${document.filename}`}>{busy === 'delete' ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : <Trash2 aria-hidden="true" />}</button>
              </li>)}</ul>
            </> : <div className="empty-block"><FolderOpen aria-hidden="true" /><p><strong>No reports yet</strong><br />Upload a PDF to keep it with this workspace.</p></div>}
            <div className="upload-zone">
              <input ref={fileInput} id="medical-pdf" type="file" accept="application/pdf,.pdf" multiple onChange={(event) => chooseFiles(Array.from(event.target.files ?? []))} />
              <label htmlFor="medical-pdf"><Upload aria-hidden="true" /><span><strong>{session.storage_provider === 'google_drive' ? 'Upload to my Drive' : 'Choose PDF reports'}</strong><small>PDF only · up to 15 MB each</small></span></label>
              {files.length > 0 && <div className="selected-files"><span>{files.length} report{files.length === 1 ? '' : 's'} ready to add</span><button className="button button-secondary" type="button" disabled={busy === 'upload'} onClick={() => void upload()}>{busy === 'upload' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Upload aria-hidden="true" className="size-4" />} Add reports</button></div>}
            </div>
          </section>

          <section className="section-card" aria-labelledby="detail-heading">
            <div className="section-heading"><div><p className="section-kicker">Structured details</p><h2 id="detail-heading">Health details</h2></div><ClipboardPlus aria-hidden="true" className="section-icon" /></div>
            {session.records.length ? <ul className="record-list">{session.records.map((record) => <li key={record.id}><span className="record-number">{categoryMeta[record.category].icon}</span><div><strong>{record.label}</strong><p>{categoryMeta[record.category].label} · added {dateLabel(record.created_at)}</p></div></li>)}</ul> : <div className="empty-block compact"><ClipboardPlus aria-hidden="true" /><p><strong>Add a health detail</strong><br />Include care visits, lab results, medications, and more.</p></div>}
            <form className="record-form" onSubmit={saveRecord} noValidate>
              <fieldset><legend>Add a detail</legend><div className="category-picker">{(Object.keys(categoryMeta) as RecordCategory[]).map((key) => <button className={category === key ? 'selected' : ''} type="button" key={key} onClick={() => { setCategory(key); setError('') }}><span>{categoryMeta[key].icon}</span>{categoryMeta[key].label}</button>)}</div></fieldset>
              <p className="field-help mb-4">{categoryMeta[category].help}</p>
              <div className="form-grid">
                {category === 'hospital' && <div className="field full"><label htmlFor="hospital">Hospital or clinic <em>Required</em></label><input id="hospital" value={form.hospital} onChange={(event) => setField('hospital', event.target.value)} placeholder="e.g. City General Hospital" /></div>}
                {needsName && <div className="field"><label htmlFor="record-name">{category === 'lab' ? 'Test name' : category === 'vital' ? 'Measurement' : 'Name'} <em>Required</em></label><input id="record-name" value={form.name} onChange={(event) => setField('name', event.target.value)} placeholder={category === 'lab' ? 'e.g. Hemoglobin A1c' : category === 'vital' ? 'e.g. Blood pressure' : 'Enter a name'} /></div>}
                {needsResult && <div className="field"><label htmlFor="record-value">Result <em>Required</em></label><input id="record-value" value={form.value} onChange={(event) => setField('value', event.target.value)} placeholder="e.g. 5.8 or Negative" /></div>}
                {needsResult && <div className="field"><label htmlFor="record-unit">Unit <span>Optional</span></label><input id="record-unit" value={form.unit} onChange={(event) => setField('unit', event.target.value)} placeholder="e.g. %, mg/dL" /></div>}
                {category === 'medication' && <div className="field"><label htmlFor="dosage">Dosage <span>Optional</span></label><input id="dosage" value={form.dosage} onChange={(event) => setField('dosage', event.target.value)} placeholder="e.g. Take once daily" /></div>}
                <div className="field"><label htmlFor="record-date">Date <span>Optional</span></label><input id="record-date" type="date" value={form.date} onChange={(event) => setField('date', event.target.value)} /></div>
                <div className="field full"><label htmlFor="details">Notes <span>Optional</span></label><textarea id="details" value={form.details} onChange={(event) => setField('details', event.target.value)} placeholder="Anything you want your clinician to know" /></div>
              </div>
              <button className="button button-secondary" type="submit" disabled={busy === 'record'}>{busy === 'record' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <Plus aria-hidden="true" className="size-4" />} Add health detail</button>
            </form>
          </section>
        </div>

        <aside className="share-card" aria-labelledby="share-heading">
          <div className="share-icon"><QrCode aria-hidden="true" /></div>
          <p className="eyebrow">Share with your clinician</p>
          <h2 id="share-heading">Create a secure QR code</h2>
          <p>It includes the {recordsAvailable} item{recordsAvailable === 1 ? '' : 's'} in this workspace. Give the clinician your passcode separately.</p>
          <label htmlFor="share-passcode">Four-digit passcode</label>
          <div className="passcode-field"><KeyRound aria-hidden="true" /><input id="share-passcode" type="password" inputMode="numeric" autoComplete="off" maxLength={4} value={sharePasscode} onChange={(event) => setSharePasscode(event.target.value.replace(/\D/g, ''))} placeholder="0000" /></div>
          <button className="button button-primary w-full" type="button" onClick={() => void generateLink()} disabled={busy === 'share' || !recordsAvailable}>{busy === 'share' ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <QrCode aria-hidden="true" className="size-4" />} Generate QR code</button>
          <p className="share-footnote"><ShieldCheck aria-hidden="true" /> The clinician needs both this QR code and your passcode.</p>
        </aside>
      </div>

      <Dialog.Root open={Boolean(link)} onOpenChange={(open) => { if (!open) setLink(null) }}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content" aria-describedby="qr-description">
            <Dialog.Close className="dialog-close" aria-label="Close QR code"><X aria-hidden="true" /></Dialog.Close>
            <p className="eyebrow">Secure link created</p>
            <Dialog.Title>Show this QR code to your clinician</Dialog.Title>
            <Dialog.Description id="qr-description">They can scan it with ClinicForce, then enter the four-digit passcode you selected.</Dialog.Description>
            {link && <img className="qr-image" src={`data:image/png;base64,${link.qr_code_base64}`} alt="Smart Health Link QR code for the current health record" />}
            <div className="dialog-note"><KeyRound aria-hidden="true" /><span>Passcode: <strong>{sharePasscode}</strong></span></div>
            <Dialog.Close className="button button-secondary w-full">Done</Dialog.Close>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  )
}
