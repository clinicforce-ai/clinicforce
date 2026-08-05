import { useState, type FormEvent } from 'react'
import Dashboard from './components/Dashboard'
import PatientPortal from './components/PatientPortal'
import Scanner from './components/Scanner'
import { decryptAndSummarize, fetchManifest, type DoctorSummary } from './services/api'
import type { SmartHealthLink } from './services/shl'
import clinicforceLogo from './assets/clinicforce-logo.png'
import './App.css'

type View = 'landing' | 'patient' | 'scan' | 'pin' | 'loading' | 'dashboard'

function App() {
  const [view, setView] = useState<View>('landing')
  const [link, setLink] = useState<SmartHealthLink | null>(null)
  const [pin, setPin] = useState('')
  const [summary, setSummary] = useState<DoctorSummary | null>(null)
  const [error, setError] = useState('')
  const handleLinkScanned = (nextLink: SmartHealthLink) => { setLink(nextLink); setError(''); setView('pin') }
  const handleSubmitPin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!link || !/^\d{4}$/.test(pin)) { setError('Enter the four-digit PIN supplied by the patient.'); return }
    setError(''); setView('loading')
    try { const jwe = await fetchManifest(link.manifestUrl, pin); setSummary(await decryptAndSummarize(jwe, link.key)); setView('dashboard') }
    catch (caughtError) { setError(caughtError instanceof Error ? caughtError.message : 'Unable to retrieve the patient file.'); setView('pin') }
  }
  const restart = () => { setView('scan'); setLink(null); setPin(''); setSummary(null); setError('') }
  const returnHome = () => { setView('landing'); setLink(null); setPin(''); setSummary(null); setError('') }
  return <main className="app-shell"><header className="app-header"><img className="brand-logo" src={clinicforceLogo} alt="ClinicForce" /><div><p className="eyebrow">Secure health records</p><p className="header-title">{view === 'patient' ? 'Patient portal' : view === 'landing' ? 'Health portal' : 'Doctor portal'}</p></div><span className="secure-badge">Secure local session</span></header>
    {view === 'landing' && <section className="landing-card" aria-labelledby="welcome-heading"><p className="step-label">Private health-data sharing</p><h1 id="welcome-heading">Welcome to ClinicForce</h1><p className="muted">Choose how you’d like to continue.</p><div className="portal-choice"><button className="portal-button patient-choice" type="button" onClick={() => setView('patient')}><span aria-hidden="true">⌁</span><strong>I am a Patient</strong><small>Upload records and create a secure QR code</small></button><button className="portal-button doctor-choice" type="button" onClick={() => setView('scan')}><span aria-hidden="true">+</span><strong>I am a Doctor</strong><small>Scan a patient’s secure QR code</small></button></div></section>}
    {view === 'patient' && <PatientPortal onBack={returnHome} />}
    {view === 'scan' && <Scanner onScanned={handleLinkScanned} onCancel={returnHome} />}
    {view === 'pin' && <section className="card pin-card" aria-labelledby="pin-heading"><p className="step-label">Step 2 of 2</p><h1 id="pin-heading">Enter patient access PIN</h1><p className="muted">The QR link was read successfully. Enter the four-digit PIN provided by the patient to open this file.</p><form onSubmit={handleSubmitPin}><label htmlFor="pin">Four-digit PIN</label><input id="pin" inputMode="numeric" autoComplete="one-time-code" maxLength={4} pattern="[0-9]{4}" placeholder="••••" value={pin} onChange={(event) => setPin(event.target.value.replace(/\D/g, ''))} autoFocus />{error && <p className="form-error" role="alert">{error}</p>}<div className="button-row"><button className="secondary-button" type="button" onClick={restart}>Scan another code</button><button className="primary-button" type="submit">Open patient file</button></div></form></section>}
    {view === 'loading' && <section className="card status-card" aria-live="polite"><span className="spinner" aria-hidden="true" /><h1>Decrypting &amp; summarizing patient file…</h1><p className="muted">This may take a moment while the local clinical summary is prepared.</p></section>}
    {view === 'dashboard' && summary && <Dashboard data={summary} onScanAnother={restart} />}
  </main>
}
export default App
