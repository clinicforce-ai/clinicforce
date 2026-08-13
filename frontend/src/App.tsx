import { lazy, Suspense, useEffect, useState, type FormEvent } from 'react'
import {
  ArrowRight,
  HeartPulse,
  LockKeyhole,
  ShieldCheck,
  Stethoscope,
  UserRound,
} from 'lucide-react'
import { decryptAndSummarize, type DoctorSummary } from './services/api'
import { manifestIdFromUrl, type SmartHealthLink } from './services/shl'
import clinicforceLogo from './assets/clinicforce-logo.png'
import './App.css'

type View = 'landing' | 'patient' | 'scan' | 'pin' | 'loading' | 'dashboard'

const Dashboard = lazy(() => import('./components/Dashboard'))
const PatientPortal = lazy(() => import('./components/PatientPortal'))
const Scanner = lazy(() => import('./components/Scanner'))

function FeatureFallback() {
  return <section className="narrow-panel" aria-live="polite"><div className="panel status-panel"><span className="spinner" aria-hidden="true" /><p className="lead-small">Loading your workspace…</p></div></section>
}

function App() {
  const [view, setView] = useState<View>(() => new URLSearchParams(window.location.search).has('patient') ? 'patient' : 'landing')
  const [link, setLink] = useState<SmartHealthLink | null>(null)
  const [pin, setPin] = useState('')
  const [summary, setSummary] = useState<DoctorSummary | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (window.location.search) window.history.replaceState({}, '', window.location.pathname)
  }, [])

  const handleLinkScanned = (nextLink: SmartHealthLink) => {
    setLink(nextLink)
    setError('')
    setView('pin')
  }

  const handleSubmitPin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!link || !/^\d{4}$/.test(pin)) {
      setError('Enter the four-digit PIN supplied by the patient.')
      return
    }
    setError('')
    setView('loading')
    try {
      setSummary(await decryptAndSummarize(manifestIdFromUrl(link.manifestUrl), link.key, pin))
      setView('dashboard')
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : 'Unable to retrieve the patient file.')
      setView('pin')
    }
  }

  const restart = () => {
    setView('scan')
    setLink(null)
    setPin('')
    setSummary(null)
    setError('')
  }

  const returnHome = () => {
    setView('landing')
    setLink(null)
    setPin('')
    setSummary(null)
    setError('')
  }

  return (
    <div className="min-h-dvh bg-[var(--canvas)] text-[var(--ink)]">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="site-header">
        <div className="shell flex min-h-20 items-center justify-between gap-4">
          <button className="brand" type="button" onClick={returnHome} aria-label="ClinicForce home">
            <img src={clinicforceLogo} alt="ClinicForce" width="180" height="48" />
            <span className="hidden border-l border-[var(--line)] pl-4 text-left text-xs font-semibold uppercase tracking-[0.15em] text-[var(--muted-ink)] sm:block">
              Personal health records
            </span>
          </button>
          <div className="flex items-center gap-2 text-sm font-semibold text-[var(--muted-ink)]">
            <ShieldCheck aria-hidden="true" className="size-5 text-[var(--green)]" />
            <span className="hidden sm:inline">Private, patient-controlled</span>
          </div>
        </div>
      </header>

      <main id="main-content" className="shell py-8 sm:py-12">
        {view === 'landing' && (
          <section className="landing-grid animate-enter" aria-labelledby="welcome-heading">
            <div className="landing-copy">
              <p className="eyebrow"><HeartPulse aria-hidden="true" className="size-4" /> One record, on your terms</p>
              <h1 id="welcome-heading">Health information that stays in your hands.</h1>
              <p className="lead">
                ClinicForce helps patients bring reports and health details together, then share an encrypted snapshot with a clinician when they choose.
              </p>
              <div className="trust-list" aria-label="How ClinicForce protects your information">
                <p><LockKeyhole aria-hidden="true" /> A passcode protects every shared link</p>
                <p><ShieldCheck aria-hidden="true" /> Source reports stay in secure document storage</p>
              </div>
            </div>
            <div className="role-panel" aria-label="Choose your portal">
              <p className="eyebrow">Choose a workspace</p>
              <button className="role-card" type="button" onClick={() => setView('patient')}>
                <span className="role-icon patient-icon"><UserRound aria-hidden="true" /></span>
                <span><strong>Patient workspace</strong><small>Add reports, organize health details, and create a QR code for your clinician.</small></span>
                <ArrowRight aria-hidden="true" className="role-arrow" />
              </button>
              <button className="role-card" type="button" onClick={() => setView('scan')}>
                <span className="role-icon doctor-icon"><Stethoscope aria-hidden="true" /></span>
                <span><strong>Clinician access</strong><small>Scan a patient’s QR code and open the health summary they chose to share.</small></span>
                <ArrowRight aria-hidden="true" className="role-arrow" />
              </button>
              <p className="mt-5 text-sm leading-6 text-[var(--muted-ink)]">Use the patient workspace to create and manage your own account.</p>
            </div>
          </section>
        )}

        <Suspense fallback={<FeatureFallback />}>
          {view === 'patient' && <PatientPortal onBack={returnHome} />}
          {view === 'scan' && <Scanner onScanned={handleLinkScanned} onCancel={returnHome} />}

        {view === 'pin' && (
          <section className="narrow-panel animate-enter" aria-labelledby="pin-heading">
            <button className="back-control" type="button" onClick={restart}>Back to scan</button>
            <div className="panel">
              <p className="eyebrow">Clinician access · Step 2 of 2</p>
              <h1 id="pin-heading">Enter the patient’s passcode</h1>
              <p className="lead-small">The QR code was read. Ask the patient for the four-digit passcode they set when sharing this record.</p>
              <form className="stack-form" onSubmit={handleSubmitPin} noValidate>
                <label htmlFor="pin">Four-digit passcode</label>
                <input
                  id="pin"
                  className="pin-input"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={4}
                  pattern="[0-9]{4}"
                  placeholder="0000"
                  value={pin}
                  onChange={(event) => setPin(event.target.value.replace(/\D/g, ''))}
                  autoFocus
                  aria-describedby={error ? 'pin-error' : undefined}
                />
                {error && <p id="pin-error" className="form-error" role="alert">{error}</p>}
                <button className="button button-primary w-full" type="submit"><LockKeyhole aria-hidden="true" className="size-4" /> Open shared record</button>
              </form>
            </div>
          </section>
        )}

        {view === 'loading' && (
          <section className="narrow-panel" aria-live="polite">
            <div className="panel status-panel">
              <span className="spinner" aria-hidden="true" />
              <p className="eyebrow">Preparing the shared record</p>
              <h1>Decrypting the clinical summary</h1>
              <p className="lead-small">This can take a moment while ClinicForce safely prepares the information for review.</p>
            </div>
          </section>
        )}

          {view === 'dashboard' && summary && <Dashboard data={summary} onScanAnother={restart} onHome={returnHome} />}
        </Suspense>
      </main>
      <footer className="shell border-t border-[var(--line)] py-6 text-sm text-[var(--muted-ink)]">
        ClinicForce · Patient-controlled record sharing
      </footer>
    </div>
  )
}

export default App
