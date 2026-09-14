import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowLeft, Camera, CheckCircle2, ShieldCheck } from 'lucide-react'
import InsuranceCardScanner from './InsuranceCardScanner'
import { ApiError } from '../services/api/client'
import { getInsuranceUploadAccount, getPatientUpload, getUploadClinic, readInsuranceUploadLocation, startPatientUpload, submitPatientUpload, type UploadClinic, type UploadReceipt } from '../services/api/insurance-upload'
import './StaffWorkspace.css'

interface Props { clinicCode?: string; uploadCode?: string; onBack: () => void }
export default function PatientInsuranceUpload({ clinicCode = '', uploadCode = '', onBack }: Props) {
  const [account, setAccount] = useState<{ available: boolean; given_name?: string; family_name?: string; birth_date?: string }>({ available: false })
  const [clinic, setClinic] = useState<UploadClinic | null>(null)
  const [token, setToken] = useState(uploadCode)
  const [expiresAt, setExpiresAt] = useState('')
  const [lookup, setLookup] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [camera, setCamera] = useState(false)
  const [busy, setBusy] = useState(Boolean(clinicCode))
  const [error, setError] = useState('')
  const [received, setReceived] = useState(false)
  const [receipt, setReceipt] = useState<UploadReceipt | null>(null)
  const [unavailable, setUnavailable] = useState(false)
  const submissionId = useRef(crypto.randomUUID())
  const inFlight = useRef(false)
  const heading = useRef<HTMLHeadingElement>(null)
  const errorBox = useRef<HTMLDivElement>(null)
  useEffect(() => { let active = true; getInsuranceUploadAccount().then(result => { if (active) setAccount(result) }).catch(() => {}); return () => { active = false } }, [])
  useEffect(() => { heading.current?.focus() }, [clinic, received, camera])
  useEffect(() => { if (error) errorBox.current?.focus() }, [error])
  useEffect(() => {
    if (!clinicCode) return
    let active = true
    const load = async () => {
      try {
        if (uploadCode) {
          const metadata = await getPatientUpload(clinicCode, uploadCode)
          if (active) { setClinic({ code: clinicCode, name: metadata.clinic_name }); setExpiresAt(metadata.expires_at); setReceived(metadata.status === 'received') }
        } else {
          const result = await getUploadClinic(clinicCode)
          if (active) setClinic(result.clinic)
        }
      } catch (caught) {
        if (active) { setError(caught instanceof Error ? caught.message : 'This upload link could not be opened.'); setUnavailable(true) }
      } finally { if (active) setBusy(false) }
    }
    void load()
    return () => { active = false }
  }, [clinicCode, uploadCode])

  async function findClinic(event: FormEvent) {
    event.preventDefault()
    const location = readInsuranceUploadLocation(lookup.trim())
    if (!location) { setError('Enter the clinic code or a ClinicForce upload link provided by your clinic.'); return }
    setBusy(true); setError('')
    try {
      if (location.token) { window.location.assign(`/${encodeURIComponent(location.code)}/${encodeURIComponent(location.token)}`); return }
      const result = await getUploadClinic(location.code)
      setClinic(result.clinic); setUnavailable(false)
      window.history.replaceState({}, '', `/${encodeURIComponent(result.clinic.code)}`)
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Clinic not found. Check the code with your clinic.') }
    finally { setBusy(false) }
  }
  function chooseFiles(next: File[]) {
    setFiles(next)
    setError(!next.length || next.length > 2 ? 'Choose one or two card files.' : next.some(file => !['image/jpeg', 'image/png', 'application/pdf'].includes(file.type) || !file.size || file.size > 10 * 1024 * 1024) ? 'Use JPG, PNG, or PDF files, each no larger than 10 MB.' : '')
  }
  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!clinic || inFlight.current) return
    if (!files.length || files.length > 2 || files.some(file => !['image/jpeg', 'image/png', 'application/pdf'].includes(file.type) || !file.size || file.size > 10 * 1024 * 1024)) { setError('Choose one or two JPG, PNG, or PDF card files, each no larger than 10 MB.'); return }
    const body = new FormData(event.currentTarget)
    files.forEach(file => body.append('files', file))
    body.set('consent', 'true'); body.set('submission_id', submissionId.current)
    inFlight.current = true; setBusy(true); setError('')
    try {
      let activeToken = token
      if (!activeToken) {
        const created = await startPatientUpload(clinic.code)
        activeToken = created.upload_code; setToken(activeToken); setExpiresAt(created.expires_at)
        window.history.replaceState({}, '', new URL(created.url, window.location.origin).pathname)
      }
      const result = await submitPatientUpload(clinic.code, activeToken, body)
      setReceipt(result); setReceived(true); setFiles([])
    } catch (caught) {
      setError(caught instanceof ApiError && [0, 408, 502, 504].includes(caught.status) ? 'We could not confirm receipt. Keep this page open and try Send to clinic again; your retry uses the same submission.' : caught instanceof Error ? caught.message : 'Your card could not be submitted. Please try again.')
    } finally { inFlight.current = false; setBusy(false) }
  }
  const alerts = <>{error && <div className="staff-alert staff-error" role="alert" tabIndex={-1} ref={errorBox}>{error}</div>}{busy && <p className="staff-progress" role="status">{clinic ? 'Sending securely. Keep this page open…' : 'Opening clinic upload…'}</p>}</>
  return <>{camera && <section className="staff-workspace staff-scan-focus"><div className="staff-scan-content"><h1 className="staff-sr-only" tabIndex={-1}>Scan your insurance card</h1><InsuranceCardScanner onCaptured={next => { chooseFiles(next); setCamera(false) }} onCancel={() => setCamera(false)} /></div></section>}<section className="staff-workspace patient-upload" hidden={camera}><div className="patient-upload-content">
    <button className="staff-text-button" onClick={onBack} disabled={busy}><ArrowLeft size={18} /> ClinicForce home</button>
    <header className="staff-header"><div><div className="staff-eyebrow">Patient insurance · No account needed</div><h1 ref={heading} tabIndex={-1}>{received ? 'Your card has been received' : 'Upload your insurance card'}</h1><p className="staff-muted">{clinic ? `Send directly to ${clinic.name}.` : 'Use the clinic code or private link your clinic provided.'}</p></div></header>
    {alerts}
    {received ? <section className="staff-panel"><CheckCircle2 className="staff-feature-icon" size={34} /><h2>Ready for clinic review</h2><p>{clinic?.name} will confirm the patient details and review the card. This does not confirm coverage or an appointment.</p>{receipt && <p className="staff-small staff-muted">Received {new Date(receipt.received_at).toLocaleString()} · Receipt {receipt.receipt_id}</p>}<p className="staff-small staff-muted">This private link has been used. Contact your clinic if you need to send a replacement.</p></section> : unavailable ? <section className="staff-panel"><h2>We could not open this upload</h2><p>Try again if your connection was interrupted. For an expired, used, or revoked link, contact your clinic for a replacement.</p><button onClick={() => window.location.reload()}>Try again</button></section> : !clinic ? !busy && <form className="staff-panel" onSubmit={findClinic}><label className="staff-field"><span>Clinic code or upload link</span><input value={lookup} onChange={event => setLookup(event.target.value)} autoComplete="off" autoCapitalize="none" spellCheck={false} required placeholder="Code from your clinic" disabled={busy} /><small>Check the clinic name on the next screen before sending your card.</small></label><button className="staff-primary" disabled={busy}>Find my clinic</button></form> : <form onSubmit={send}>
      <section className="staff-panel"><div className="staff-section-heading"><h2>Your insurance card</h2><ShieldCheck size={22} aria-hidden="true" /></div><p className="staff-muted">Add the front and back if available. Use a clear photo or a card PDF.</p><button type="button" onClick={() => setCamera(true)} disabled={busy}><Camera size={18} /> Scan with camera</button><label className="staff-field patient-card-input"><span>Or choose card files</span><input type="file" accept="image/jpeg,image/png,application/pdf" multiple onChange={event => chooseFiles(Array.from(event.target.files || []))} disabled={busy} /><small>JPG, PNG, or PDF · Up to 2 files and 2 pages total · 10 MB per file.</small></label>{files.length > 0 && <ul className="patient-file-list">{files.map((file, index) => <li key={`${file.name}-${index}`}>{file.name}</li>)}</ul>}</section>
      <section className="staff-panel"><h2>Patient receiving care</h2><p className="staff-small staff-muted">Enter the patient's details, even if the card shows a different policyholder.</p><div className="staff-two-columns"><label className="staff-field"><span>First name *</span><input name="given_name" autoComplete="given-name" maxLength={100} required disabled={busy} /></label><label className="staff-field"><span>Last name *</span><input name="family_name" autoComplete="family-name" maxLength={100} required disabled={busy} /></label><label className="staff-field"><span>Date of birth *</span><input name="birth_date" type="date" autoComplete="bday" min="1900-01-01" max={new Date().toLocaleDateString('en-CA')} required disabled={busy} /></label><label className="staff-field"><span>Member ID, if available</span><input name="member_id" autoComplete="off" maxLength={80} disabled={busy} /></label><label className="staff-field"><span>Contact email for your clinic (optional)</span><input name="email" type="email" autoComplete="email" maxLength={254} disabled={busy} /><small>Your clinic can use this address to contact you about this submission.</small></label></div>{account.available && <label className="staff-confirmation"><input type="checkbox" name="link_account" value="true" disabled={busy} /><span>Also connect this submission to my signed-in ClinicForce account: {account.given_name} {account.family_name}{account.birth_date ? ` · ${account.birth_date}` : ''}. The patient name and date of birth must match this account.</span></label>}<label className="staff-confirmation"><input type="checkbox" required disabled={busy} /><span>I am the patient or am authorized to submit on their behalf. I agree to send these details and card files to {clinic.name} for insurance review.</span></label><button className="staff-primary staff-full" disabled={busy || !files.length}>Send to clinic</button>{expiresAt && <p className="staff-small staff-muted patient-expiry">Link expires {new Date(expiresAt).toLocaleString()}.</p>}</section>
    </form>}
    <p className="staff-small staff-muted">A clinic patient record is created automatically. No password or sign-in is needed.</p>
  </div></section></>
}
